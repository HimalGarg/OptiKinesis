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
