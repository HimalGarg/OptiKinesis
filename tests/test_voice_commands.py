import unittest
from unittest import mock
import threading

from voice_commands import ActionType, ReminderScheduler, VoiceCommandExecutor, VoiceCommandParser


class VoiceCommandTests(unittest.TestCase):
    def test_parser_extracts_google_query(self):
        intent = VoiceCommandParser().parse("Search accessible keyboards on Google")
        self.assertEqual(intent.action_type, ActionType.SEARCH_GOOGLE)
        self.assertEqual(intent.params["query"], "accessible keyboards")
        self.assertFalse(intent.requires_confirmation)

    def test_email_requires_confirmation(self):
        executor = VoiceCommandExecutor()
        command = "Send email to helper@example.com, subject Update, content I am Safe"
        intent = executor.parser.parse(command)
        result = executor.process_command(command)
        self.assertEqual(result["status"], "pending_confirmation")
        self.assertEqual(result["action_type"], "send_email")
        self.assertEqual(intent.params["subject"], "Update")
        self.assertEqual(intent.params["body"], "I am Safe")

    def test_confirmed_alarm_is_scheduled(self):
        executor = VoiceCommandExecutor()
        with mock.patch.object(
            executor.reminder_scheduler,
            "schedule_alarm",
            return_value="Alarm set for 7:00 AM",
        ) as schedule:
            pending = executor.process_command("Set alarm for 7 AM")
            result = executor.confirm_pending()
        self.assertEqual(pending["status"], "pending_confirmation")
        self.assertEqual(result["status"], "success")
        schedule.assert_called_once_with("7 AM")

    def test_questions_and_find_phrases_are_searches(self):
        parser = VoiceCommandParser()
        cases = {
            "What is ALS": "What is ALS",
            "find wheelchair ramps near me": "wheelchair ramps near me",
            "look up physiotherapy exercises": "physiotherapy exercises",
        }
        for spoken, query in cases.items():
            intent = parser.parse(spoken)
            self.assertEqual(intent.action_type, ActionType.SEARCH_GOOGLE, spoken)
            self.assertEqual(intent.params["query"], query)
        self.assertEqual(parser.parse("find relaxing music on youtube").action_type,
                         ActionType.SEARCH_YOUTUBE)

    def test_search_shows_answer_cards_when_overlay_handles_it(self):
        executor = VoiceCommandExecutor()
        seen = []
        executor.set_web_handlers(search=lambda q: seen.append(q) or True)
        with mock.patch("voice_commands.webbrowser.open") as browser:
            result = executor.process_command("search ALS trials on Google")
        self.assertEqual(seen, ["ALS trials"])
        self.assertIn("Showing answers", result["message"])
        browser.assert_not_called()

    def test_search_falls_back_to_google_without_handler(self):
        executor = VoiceCommandExecutor()
        with mock.patch("voice_commands.webbrowser.open") as browser:
            result = executor.process_command("google ALS trials")
        browser.assert_called_once()
        self.assertEqual(result["status"], "success")

    def test_read_this_page_uses_reader_handler(self):
        executor = VoiceCommandExecutor()
        calls = []
        executor.set_web_handlers(read_page=lambda: calls.append(1) or True)
        result = executor.process_command("Read this page")
        self.assertEqual(calls, [1])
        self.assertEqual(result["status"], "success")
        self.assertEqual(VoiceCommandExecutor().process_command("read this page")["status"], "error")

    def test_alarm_parser_rejects_unstructured_time(self):
        with self.assertRaises(ValueError):
            ReminderScheduler._parse_alarm_time("sometime tomorrow")

    def test_reminder_invokes_callback_and_removes_timer(self):
        scheduler = ReminderScheduler()
        triggered = threading.Event()
        messages = []
        scheduler.set_callback(lambda message: (messages.append(message), triggered.set()))
        scheduler.schedule_reminder(0.01, "Time to rest")
        self.assertTrue(triggered.wait(1.0))
        self.assertEqual(messages, ["Time to rest"])
        self.assertEqual(scheduler.active_reminders, [])


if __name__ == "__main__":
    unittest.main()
