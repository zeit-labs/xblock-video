"""
Test cases for VideoXBlock handlers
"""

import json

from mock import patch, Mock, PropertyMock

from video_xblock import VideoXBlock
from video_xblock.tests.unit.base import VideoXBlockTestBase, arrange_request_mock


class AuthenticateApiHandlerTests(VideoXBlockTestBase):  # pylint: disable=test-inherits-tests
    """
    Test cases for `VideoXBlock.authenticate_video_api_handler`.
    """

    @patch.object(VideoXBlock, 'authenticate_video_api')
    def test_auth_video_api_handler_delegates_call(self, auth_video_api_mock):
        """
        Test xBlock's video API authentication works properly.
        """
        # Arrange
        request_mock = arrange_request_mock('"test-token-123"')  # JSON string
        auth_video_api_mock.return_value = {}, ''

        # Act
        result_response = self.xblock.authenticate_video_api_handler(request_mock)
        result = result_response.body  # pylint: disable=no-member

        # Assert
        self.assertEqual(
            result,
            json.dumps({'success_message': 'Successfully authenticated to the video platform.'}).encode()
        )
        auth_video_api_mock.assert_called_once_with('test-token-123')  # Python string


class UploadDefaultTranscriptHandlerTests(VideoXBlockTestBase):  # pylint: disable=test-inherits-tests
    """
    Test cases for `VideoXBlock.upload_default_transcript_handler`.
    """

    @patch('video_xblock.video_xblock.create_reference_name')
    def test_upload_handler_default_transcript_not_in_vtt_case(self, create_reference_name_mock):
        """
        Test xBlock's handler for default transcripts uploading.
        """
        # Arrange
        request_body = """{"label": "test_label","lang": "test_lang","source": "test_source","url": "test_url"}"""
        assert_data = json.loads(request_body)
        test_media_id = 'test_video_id'
        test_subs_text = 'test_subs_text'
        test_reference = 'test_reference'
        test_file_name = 'test_file_name'
        test_external_url = 'test_external_url'

        request_mock = arrange_request_mock(request_body)
        create_reference_name_mock.return_value = test_reference

        with patch.object(self.xblock, 'get_player') as get_player_mock, \
                patch.object(self.xblock, 'convert_caps_to_vtt') as convert_caps_mock, \
                patch.object(self.xblock, 'create_transcript_file') as create_transcript_file_mock:

            get_player_mock.return_value = player_mock = Mock()
            convert_caps_mock.return_value = prepared_subs_mock = Mock()
            create_transcript_file_mock.return_value = (test_file_name, test_external_url)

            player_mock.media_id.return_value = test_media_id
            player_mock.download_default_transcript.return_value = test_subs_text
            type(player_mock).default_transcripts_in_vtt = PropertyMock(return_value=False)

            # Act
            response = self.xblock.upload_default_transcript_handler(request_mock)

            # Assert
            player_mock.download_default_transcript.assert_called_with(
                assert_data['url'], assert_data['lang']
            )
            create_reference_name_mock.assert_called_with(assert_data['label'], test_media_id, assert_data['source'])
            player_mock.download_default_transcript.assert_called_with(
                assert_data['url'], assert_data['lang']
            )
            convert_caps_mock.assert_called_with(caps=test_subs_text)
            create_transcript_file_mock.assert_called_with(trans_str=prepared_subs_mock, reference_name=test_reference)
            self.assertEqual(
                response.body.decode(),  # pylint: disable=no-member
                json.dumps({
                    'success_message': 'Successfully uploaded "test_file_name".',
                    'lang': assert_data['lang'],
                    'url': test_external_url,
                    'label': assert_data['label'],
                    'source': assert_data['source'],
                })
            )


class UpdateProgressHandlerTests(VideoXBlockTestBase):  # pylint: disable=test-inherits-tests
    """
    Test cases for `VideoXBlock.update_progress` completion publish/retry.
    """

    def _call_update_progress(self, current_time, duration, completion_service=None, extra=None, settings=None):
        """Helper to POST progress and optionally stub the completion service."""
        def service(_block, name):
            if name == 'completion':
                return completion_service
            return Mock()

        self.xblock.runtime.service = Mock(side_effect=service)
        data = {
            'current_time': current_time,
            'duration': duration,
        }
        data.update(extra or {})
        request = arrange_request_mock(json.dumps(data))
        with patch.object(VideoXBlock, 'settings', new_callable=PropertyMock) as settings_mock:
            settings_mock.return_value = settings or {}
            response = self.xblock.update_progress(request)
        return json.loads(response.body.decode())  # pylint: disable=no-member

    def test_threshold_crossing_submits_completion(self):
        """First threshold crossing publishes completion and sets completion_published."""
        self.xblock.completion_threshold = 70
        self.xblock.watch_progress = 0.0
        self.xblock.completion_published = False
        completion_service = Mock()

        result = self._call_update_progress(80, 100, completion_service=completion_service)

        completion_service.submit_completion.assert_called_once_with(
            block_key=self.xblock.scope_ids.usage_id,
            completion=1.0,
        )
        self.assertTrue(self.xblock.completion_published)
        self.assertTrue(result['completed'])
        self.assertTrue(result['completion_published'])
        self.assertEqual(result['watch_progress'], 0.8)

    def test_submit_failure_keeps_completion_unpublished(self):
        """Failed submit still saves watch_progress but leaves completion_published false."""
        self.xblock.completion_threshold = 70
        self.xblock.watch_progress = 0.0
        self.xblock.completion_published = False
        completion_service = Mock()
        completion_service.submit_completion.side_effect = RuntimeError('lms down')

        result = self._call_update_progress(80, 100, completion_service=completion_service)

        self.assertEqual(self.xblock.watch_progress, 0.8)
        self.assertFalse(self.xblock.completion_published)
        self.assertTrue(result['completed'])
        self.assertFalse(result['completion_published'])

    def test_retries_when_watch_progress_already_past_threshold(self):
        """Stuck learners with high watch_progress but no publish retry on later pings."""
        self.xblock.completion_threshold = 70
        self.xblock.watch_progress = 0.95
        self.xblock.completion_published = False
        completion_service = Mock()

        result = self._call_update_progress(95, 100, completion_service=completion_service)

        completion_service.submit_completion.assert_called_once_with(
            block_key=self.xblock.scope_ids.usage_id,
            completion=1.0,
        )
        self.assertTrue(self.xblock.completion_published)
        self.assertTrue(result['completion_published'])

    def test_already_published_does_not_resubmit(self):
        """Successful publish is not repeated on subsequent pings."""
        self.xblock.completion_threshold = 70
        self.xblock.watch_progress = 0.95
        self.xblock.completion_published = True
        completion_service = Mock()

        result = self._call_update_progress(95, 100, completion_service=completion_service)

        completion_service.submit_completion.assert_not_called()
        self.assertTrue(result['completed'])
        self.assertTrue(result['completion_published'])

    def test_timestamp_ping_does_not_force_completion(self):
        """A ping without the ended flag keeps using current_time for progress."""
        self.xblock.completion_threshold = 100
        self.xblock.watch_progress = 0.0
        self.xblock.completion_published = False

        result = self._call_update_progress(30, 100, completion_service=Mock())

        self.assertEqual(self.xblock.watch_progress, 0.3)
        self.assertFalse(result['completed'])

    def test_ended_flag_forces_full_progress(self):
        """ended=true marks the video finished even at a 100% threshold."""
        self.xblock.completion_threshold = 100
        self.xblock.watch_progress = 0.0
        self.xblock.completion_published = False
        self.xblock.max_played_time = 0.0
        completion_service = Mock()

        result = self._call_update_progress(
            0, 75.123, completion_service=completion_service, extra={'ended': True}
        )

        self.assertEqual(self.xblock.watch_progress, 1.0)
        self.assertEqual(self.xblock.max_played_time, 75.123)
        self.assertEqual(self.xblock.last_position, 75.123)
        self.assertTrue(result['completed'])
        completion_service.submit_completion.assert_called_once_with(
            block_key=self.xblock.scope_ids.usage_id,
            completion=1.0,
        )

    def test_ended_flag_forces_full_progress_with_max_time_for_progress(self):
        """ended=true advances max_played_time to duration when anti-skip is enabled."""
        self.xblock.completion_threshold = 80
        self.xblock.watch_progress = 0.0
        self.xblock.completion_published = False
        self.xblock.max_played_time = 12.0

        result = self._call_update_progress(
            0, 90.5, completion_service=Mock(),
            extra={'ended': True}, settings={'max_time_for_progress': True},
        )

        self.assertEqual(self.xblock.max_played_time, 90.5)
        self.assertEqual(self.xblock.watch_progress, 1.0)
        self.assertTrue(result['completed'])

    def test_ended_flag_without_duration_keeps_state(self):
        """ended=true with an unusable duration leaves progress and completion untouched."""
        self.xblock.completion_threshold = 80
        self.xblock.watch_progress = 0.4
        self.xblock.completion_published = False
        self.xblock.max_played_time = 40.0

        result = self._call_update_progress(0, 0, completion_service=Mock(), extra={'ended': True})

        self.assertEqual(self.xblock.watch_progress, 0.4)
        self.assertFalse(result['completed'])
        self.assertFalse(result['completion_published'])
