"""
Voice-Based Daily Task Automation for Senseway

This module provides voice command processing for common daily tasks,
reducing the need for repetitive eye-gaze and blink interactions.

Supported Commands:
- "Send an email to [address], subject [subject], content [message]"
- "Set an alarm for [time]"
- "Set a reminder in [X minutes/hours]"
- "Search [query] on Google"
- "Open YouTube and search [query]"

Design Philosophy:
- Reduce physical effort for users with motor disabilities
- Require confirmation for critical actions (email, alarms)
- Complement (not replace) gaze-based interaction
"""

import re
import time
import smtplib
import webbrowser
import urllib.parse
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any, Optional, Callable, List, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

logging.basicConfig(level=logging.INFO)
voice_logger = logging.getLogger('voice_commands')


class ActionType(Enum):
    """Types of voice command actions."""
    SEARCH_GOOGLE = "search_google"
    SEARCH_YOUTUBE = "search_youtube"
    SEND_EMAIL = "send_email"
    SET_ALARM = "set_alarm"
    SET_REMINDER = "set_reminder"
    UNKNOWN = "unknown"


@dataclass
class ParsedIntent:
    """Represents a parsed voice command intent."""
    action_type: ActionType
    params: Dict[str, Any]
    original_text: str
    requires_confirmation: bool
    confidence: float  # 0.0 to 1.0


@dataclass
class PendingAction:
    """An action awaiting user confirmation."""
    intent: ParsedIntent
    created_at: float
    expires_at: float
    description: str


class VoiceCommandParser:
    """
    Parses voice input into structured intents using regex patterns.
    
    Uses pattern matching with fallback for flexible phrasing.
    Future enhancement: LLM-based intent extraction.
    """
    
    # Regex patterns for intent detection (case-insensitive)
    PATTERNS = [
        # Email: "send email to X, subject Y, content Z" or variations
        (
            r"send\s+(?:an?\s+)?email\s+to\s+([^\s,]+@[^\s,]+)[\s,]+subject\s+(.+?)[\s,]+(?:content|message|body)\s+(.+)",
            ActionType.SEND_EMAIL,
            ['recipient', 'subject', 'body'],
            True  # requires confirmation
        ),
        # Simpler email: "email X saying Y"
        (
            r"email\s+([^\s,]+@[^\s,]+)\s+(?:saying|with|message)\s+(.+)",
            ActionType.SEND_EMAIL,
            ['recipient', 'body'],
            True
        ),
        # Alarm: "set alarm for 7:30 AM" or "alarm at 3pm"
        (
            r"(?:set\s+)?(?:an?\s+)?alarm\s+(?:for|at)\s+(.+)",
            ActionType.SET_ALARM,
            ['time'],
            True
        ),
        # Reminder: "set reminder in 5 minutes" or "remind me in 1 hour"
        (
            r"(?:set\s+)?(?:a\s+)?reminder\s+in\s+(\d+)\s*(minutes?|hours?|mins?|hrs?)",
            ActionType.SET_REMINDER,
            ['amount', 'unit'],
            True
        ),
        (
            r"remind\s+me\s+in\s+(\d+)\s*(minutes?|hours?|mins?|hrs?)",
            ActionType.SET_REMINDER,
            ['amount', 'unit'],
            True
        ),
        # Google search: "search X on google" or "google X"
        (
            r"search\s+(.+?)\s+on\s+google",
            ActionType.SEARCH_GOOGLE,
            ['query'],
            False
        ),
        (
            r"google\s+(.+)",
            ActionType.SEARCH_GOOGLE,
            ['query'],
            False
        ),
        # YouTube: "open youtube and search X" or "youtube X"
        (
            r"(?:open\s+)?youtube\s+(?:and\s+)?search\s+(.+)",
            ActionType.SEARCH_YOUTUBE,
            ['query'],
            False
        ),
        (
            r"(?:play|watch|find)\s+(.+)\s+on\s+youtube",
            ActionType.SEARCH_YOUTUBE,
            ['query'],
            False
        ),
    ]
    
    def parse(self, text: str) -> ParsedIntent:
        """
        Parse voice text into a structured intent.
        
        Args:
            text: The transcribed voice command
            
        Returns:
            ParsedIntent with action type, parameters, and confidence
        """
        text_lower = text.lower().strip()
        
        for pattern, action_type, param_names, requires_confirm in self.PATTERNS:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                params = {}
                for i, name in enumerate(param_names):
                    if i < len(match.groups()):
                        params[name] = match.group(i + 1).strip()
                
                # Calculate confidence based on match quality
                confidence = self._calculate_confidence(match, text_lower)
                
                return ParsedIntent(
                    action_type=action_type,
                    params=params,
                    original_text=text,
                    requires_confirmation=requires_confirm,
                    confidence=confidence
                )
        
        # No pattern matched
        return ParsedIntent(
            action_type=ActionType.UNKNOWN,
            params={},
            original_text=text,
            requires_confirmation=False,
            confidence=0.0
        )
    
    def _calculate_confidence(self, match: re.Match, text: str) -> float:
        """Calculate confidence score based on match coverage."""
        matched_length = len(match.group(0))
        total_length = len(text)
        return min(1.0, matched_length / total_length)


class EmailHandler:
    """
    Handles email sending via SMTP.
    
    Requires configuration of SMTP credentials.
    """
    
    def __init__(self, config: Optional[Dict[str, str]] = None):
        """
        Initialize with optional SMTP configuration.
        
        Args:
            config: Dict with 'smtp_server', 'smtp_port', 'email', 'password'
        """
        self.config = config or {}
        self.is_configured = all(
            key in self.config 
            for key in ['smtp_server', 'smtp_port', 'email', 'password']
        )
    
    def send(self, recipient: str, subject: str, body: str) -> Tuple[bool, str]:
        """
        Send an email.
        
        Args:
            recipient: Email address to send to
            subject: Email subject line
            body: Email body text
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        if not self.is_configured:
            return False, "Email not configured. Please set SMTP credentials."
        
        try:
            msg = MIMEMultipart()
            msg['From'] = self.config['email']
            msg['To'] = recipient
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))
            
            with smtplib.SMTP(
                self.config['smtp_server'], 
                int(self.config['smtp_port'])
            ) as server:
                server.starttls()
                server.login(self.config['email'], self.config['password'])
                server.send_message(msg)
            
            voice_logger.info("Email sent to %s: %s", recipient, subject)
            return True, f"Email sent to {recipient}"
            
        except Exception as e:
            voice_logger.error("Failed to send email: %s", str(e))
            return False, f"Failed to send email: {str(e)}"


class ReminderScheduler:
    """
    Schedules reminders and alarms using threading timers.
    
    Note: Reminders are in-memory and won't persist across restarts.
    For production, consider using a persistent scheduler like APScheduler.
    """
    
    def __init__(self):
        self.active_reminders: List[Tuple[threading.Timer, str]] = []
        self.reminder_callback: Optional[Callable[[str], None]] = None
    
    def set_callback(self, callback: Callable[[str], None]) -> None:
        """Set the callback function for when reminders trigger."""
        self.reminder_callback = callback
    
    def schedule_reminder(self, delay_seconds: float, message: str = "Reminder!") -> str:
        """
        Schedule a reminder.
        
        Args:
            delay_seconds: Seconds until reminder triggers
            message: Message to display when reminder fires
            
        Returns:
            Confirmation message
        """
        def trigger():
            voice_logger.info("Reminder triggered: %s", message)
            if self.reminder_callback:
                self.reminder_callback(message)
        
        timer = threading.Timer(delay_seconds, trigger)
        timer.daemon = True
        timer.start()
        
        self.active_reminders.append((timer, message))
        
        # Format human-readable time
        if delay_seconds >= 3600:
            time_str = f"{delay_seconds / 3600:.1f} hours"
        elif delay_seconds >= 60:
            time_str = f"{delay_seconds / 60:.0f} minutes"
        else:
            time_str = f"{delay_seconds:.0f} seconds"
        
        voice_logger.info("Reminder scheduled in %s: %s", time_str, message)
        return f"Reminder set for {time_str}"
    
    def cancel_all(self) -> None:
        """Cancel all pending reminders."""
        for timer, _ in self.active_reminders:
            timer.cancel()
        self.active_reminders.clear()
        voice_logger.info("All reminders cancelled")


class VoiceCommandExecutor:
    """
    Executes parsed voice command intents.
    
    Handles both immediate actions (search) and confirmed actions (email, reminder).
    """
    
    def __init__(self):
        self.parser = VoiceCommandParser()
        self.email_handler = EmailHandler()
        self.reminder_scheduler = ReminderScheduler()
        
        # Pending action awaiting confirmation
        self.pending_action: Optional[PendingAction] = None
        self._lock = threading.Lock()
        
        # Confirmation timeout (seconds)
        self.confirmation_timeout = 30
    
    def configure_email(self, config: Dict[str, str]) -> None:
        """Configure email SMTP settings."""
        self.email_handler = EmailHandler(config)
    
    def set_reminder_callback(self, callback: Callable[[str], None]) -> None:
        """Set callback for when reminders trigger."""
        self.reminder_scheduler.set_callback(callback)
    
    def process_command(self, text: str) -> Dict[str, Any]:
        """
        Process a voice command text.
        
        Args:
            text: Transcribed voice command
            
        Returns:
            Dict with status, action type, and any pending confirmation
        """
        intent = self.parser.parse(text)
        
        if intent.action_type == ActionType.UNKNOWN:
            return {
                'status': 'unknown',
                'message': "I didn't understand that command. Try: 'Search X on Google' or 'Set reminder in 5 minutes'",
                'original': text
            }
        
        if intent.requires_confirmation:
            # Queue for confirmation
            return self._queue_for_confirmation(intent)
        else:
            # Execute immediately
            return self._execute_intent(intent)
    
    def _queue_for_confirmation(self, intent: ParsedIntent) -> Dict[str, Any]:
        """Queue an action for user confirmation."""
        with self._lock:
            current_time = time.time()
            
            # Create human-readable description
            description = self._describe_action(intent)
            
            self.pending_action = PendingAction(
                intent=intent,
                created_at=current_time,
                expires_at=current_time + self.confirmation_timeout,
                description=description
            )
            
            return {
                'status': 'pending_confirmation',
                'action_type': intent.action_type.value,
                'description': description,
                'timeout': self.confirmation_timeout,
                'message': f"Please confirm: {description}"
            }
    
    def _describe_action(self, intent: ParsedIntent) -> str:
        """Create human-readable description of an action."""
        params = intent.params
        
        if intent.action_type == ActionType.SEND_EMAIL:
            recipient = params.get('recipient', 'unknown')
            subject = params.get('subject', 'No subject')
            return f"Send email to {recipient} with subject: {subject}"
        
        elif intent.action_type == ActionType.SET_ALARM:
            time_str = params.get('time', 'unknown time')
            return f"Set alarm for {time_str}"
        
        elif intent.action_type == ActionType.SET_REMINDER:
            amount = params.get('amount', '?')
            unit = params.get('unit', 'minutes')
            return f"Set reminder in {amount} {unit}"
        
        return f"Execute {intent.action_type.value}"
    
    def confirm_pending(self) -> Dict[str, Any]:
        """Confirm and execute the pending action."""
        with self._lock:
            if not self.pending_action:
                return {'status': 'error', 'message': 'No pending action to confirm'}
            
            if time.time() > self.pending_action.expires_at:
                self.pending_action = None
                return {'status': 'expired', 'message': 'Confirmation timed out'}
            
            intent = self.pending_action.intent
            self.pending_action = None
            
            return self._execute_intent(intent)
    
    def cancel_pending(self) -> Dict[str, Any]:
        """Cancel the pending action."""
        with self._lock:
            if not self.pending_action:
                return {'status': 'ok', 'message': 'No pending action'}
            
            description = self.pending_action.description
            self.pending_action = None
            
            return {'status': 'cancelled', 'message': f'Cancelled: {description}'}
    
    def get_pending(self) -> Optional[Dict[str, Any]]:
        """Get the current pending action if any."""
        with self._lock:
            if not self.pending_action:
                return None
            
            if time.time() > self.pending_action.expires_at:
                self.pending_action = None
                return None
            
            remaining = self.pending_action.expires_at - time.time()
            return {
                'description': self.pending_action.description,
                'action_type': self.pending_action.intent.action_type.value,
                'timeout_remaining': round(remaining, 1)
            }
    
    def _execute_intent(self, intent: ParsedIntent) -> Dict[str, Any]:
        """Execute a parsed intent."""
        params = intent.params
        
        try:
            if intent.action_type == ActionType.SEARCH_GOOGLE:
                query = params.get('query', '')
                url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
                webbrowser.open(url)
                return {'status': 'success', 'message': f'Searching Google for: {query}'}
            
            elif intent.action_type == ActionType.SEARCH_YOUTUBE:
                query = params.get('query', '')
                url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
                webbrowser.open(url)
                return {'status': 'success', 'message': f'Searching YouTube for: {query}'}
            
            elif intent.action_type == ActionType.SEND_EMAIL:
                recipient = params.get('recipient', '')
                subject = params.get('subject', 'No Subject')
                body = params.get('body', '')
                
                success, message = self.email_handler.send(recipient, subject, body)
                status = 'success' if success else 'error'
                return {'status': status, 'message': message}
            
            elif intent.action_type == ActionType.SET_REMINDER:
                amount = int(params.get('amount', 0))
                unit = params.get('unit', 'minutes').lower()
                
                # Convert to seconds
                if 'hour' in unit or 'hr' in unit:
                    delay = amount * 3600
                else:
                    delay = amount * 60
                
                message = self.reminder_scheduler.schedule_reminder(delay)
                return {'status': 'success', 'message': message}
            
            elif intent.action_type == ActionType.SET_ALARM:
                # For now, treat alarm same as reminder with parsed time
                # Full alarm implementation would require time parsing
                time_str = params.get('time', '')
                return {
                    'status': 'info', 
                    'message': f'Alarm for {time_str} noted. (Full alarm scheduling coming soon)'
                }
            
            return {'status': 'error', 'message': 'Unknown action type'}
            
        except Exception as e:
            voice_logger.error("Error executing intent: %s", str(e))
            return {'status': 'error', 'message': str(e)}


# Global instance for easy access from main.py
voice_executor = VoiceCommandExecutor()
