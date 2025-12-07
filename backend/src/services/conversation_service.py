"""
Conversation service for managing AI-guided diary conversations.
Handles dialogue flow, context management, and response generation.
"""
from typing import List, Dict, Optional
from datetime import datetime
from dataclasses import dataclass, field

from src.services.llm_service import llm_service, MessageRole


# System prompt for the diary assistant
DIARY_ASSISTANT_PROMPT = """You are a warm, empathetic AI companion helping the user reflect on their day through conversation. Your role is to:

1. Guide the user through daily reflection in a natural, conversational way
2. Ask thoughtful follow-up questions to help them explore their thoughts and feelings
3. Show genuine interest and empathy in their experiences
4. Help them identify patterns, insights, and things they're grateful for
5. Keep responses concise (2-3 sentences) since this is a voice conversation

Conversation style:
- Warm and supportive, like a caring friend
- Ask one question at a time
- Acknowledge their feelings before asking follow-ups
- Use natural, conversational language (not formal or clinical)
- Gently guide them to reflect deeper when appropriate

Start by warmly greeting them and asking how their day was. As the conversation progresses, explore:
- What happened today (events, interactions)
- How they felt about these experiences
- What they learned or realized
- What they're looking forward to or worried about

Keep the conversation flowing naturally. When they seem ready to wrap up, help them identify one key takeaway or intention for tomorrow."""


@dataclass
class ConversationMessage:
    """A single message in the conversation."""
    role: str  # "user", "assistant", "system"
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ConversationContext:
    """Maintains conversation state during a call."""
    call_id: Optional[str] = None
    user_id: Optional[int] = None
    messages: List[ConversationMessage] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.utcnow)
    is_ending: bool = False

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the conversation."""
        self.messages.append(ConversationMessage(role=role, content=content))

    def get_messages_for_llm(self) -> List[Dict[str, str]]:
        """Format messages for LLM API."""
        return [{"role": msg.role, "content": msg.content} for msg in self.messages]

    def get_transcript(self) -> str:
        """Get full conversation transcript."""
        lines = []
        for msg in self.messages:
            if msg.role != "system":
                role = "AI" if msg.role == "assistant" else "User"
                lines.append(f"{role}: {msg.content}")
        return "\n".join(lines)

    def get_user_utterances(self) -> List[str]:
        """Get only user messages for diary generation."""
        return [msg.content for msg in self.messages if msg.role == "user"]


class ConversationService:
    """Service for managing AI-guided conversations."""

    def __init__(self, system_prompt: Optional[str] = None):
        """Initialize conversation service."""
        self.system_prompt = system_prompt or DIARY_ASSISTANT_PROMPT
        self.active_conversations: Dict[str, ConversationContext] = {}

    def start_conversation(
        self,
        call_id: str,
        user_id: Optional[int] = None
    ) -> ConversationContext:
        """
        Start a new conversation.

        Args:
            call_id: Unique call identifier
            user_id: Optional user ID for personalization

        Returns:
            New ConversationContext
        """
        context = ConversationContext(call_id=call_id, user_id=user_id)
        # Add system prompt
        context.add_message("system", self.system_prompt)
        self.active_conversations[call_id] = context
        return context

    def get_conversation(self, call_id: str) -> Optional[ConversationContext]:
        """Get active conversation by call ID."""
        return self.active_conversations.get(call_id)

    def end_conversation(self, call_id: str) -> Optional[ConversationContext]:
        """
        End and remove a conversation.

        Args:
            call_id: Call identifier

        Returns:
            The ended conversation context
        """
        return self.active_conversations.pop(call_id, None)

    async def generate_greeting(self, context: ConversationContext) -> str:
        """
        Generate initial greeting message.

        Args:
            context: Conversation context

        Returns:
            Greeting text
        """
        # Generate contextual greeting based on time of day
        hour = datetime.now().hour
        if hour < 12:
            time_greeting = "Good morning"
        elif hour < 17:
            time_greeting = "Good afternoon"
        else:
            time_greeting = "Good evening"

        greeting = f"{time_greeting}! I'm here to help you reflect on your day. How has your day been so far?"
        context.add_message("assistant", greeting)
        return greeting

    async def generate_response(
        self,
        context: ConversationContext,
        user_input: str
    ) -> str:
        """
        Generate AI response to user input.

        Args:
            context: Conversation context
            user_input: What the user said

        Returns:
            AI response text
        """
        # Add user message to context
        context.add_message("user", user_input)

        # Check if user wants to end
        end_phrases = ["goodbye", "bye", "that's all", "i'm done", "end", "finish"]
        if any(phrase in user_input.lower() for phrase in end_phrases):
            context.is_ending = True
            response = await self._generate_closing_response(context)
        else:
            # Generate regular response
            response = await llm_service.generate_response(
                messages=context.get_messages_for_llm(),
                temperature=0.8,
                max_tokens=150  # Keep responses concise for voice
            )

        # Add assistant response to context
        context.add_message("assistant", response)
        return response

    async def _generate_closing_response(self, context: ConversationContext) -> str:
        """Generate a closing response that summarizes the conversation."""
        closing_prompt = context.get_messages_for_llm()
        closing_prompt.append({
            "role": "user",
            "content": "Please provide a brief, warm closing that acknowledges what we discussed and wishes them well. Keep it to 2-3 sentences."
        })

        response = await llm_service.generate_response(
            messages=closing_prompt,
            temperature=0.7,
            max_tokens=100
        )
        return response

    async def generate_diary_entry(
        self,
        context: ConversationContext
    ) -> Dict[str, any]:
        """
        Generate a diary entry from the conversation, written from user's perspective.

        Args:
            context: Completed conversation context

        Returns:
            Dict with diary entry content
        """
        transcript = context.get_transcript()

        diary_prompt = f"""Based on the following conversation between a user and their AI diary companion,
generate a personal diary entry written from the USER's perspective (first person).

The diary entry should:
1. Be written as if the user wrote it themselves ("I felt...", "Today I...")
2. Capture the key events, thoughts, and feelings they shared
3. Include any insights or realizations from the conversation
4. Be warm and personal in tone
5. Be 2-4 paragraphs long

Conversation transcript:
{transcript}

Generate the diary entry in JSON format:
{{
    "title": "A meaningful title for this entry",
    "date": "{datetime.now().strftime('%B %d, %Y')}",
    "content": "The diary entry text written in first person...",
    "mood": "The overall mood (e.g., reflective, grateful, anxious, hopeful, tired)",
    "highlights": ["Key moment or thought 1", "Key moment or thought 2"],
    "gratitude": ["Something they're grateful for if mentioned"],
    "tomorrow_intention": "Any intention or goal for tomorrow if discussed"
}}

Return ONLY valid JSON, no markdown."""

        messages = [
            {"role": "system", "content": "You are a skilled writer who transforms conversations into personal diary entries."},
            {"role": "user", "content": diary_prompt}
        ]

        response = await llm_service.generate_response(
            messages=messages,
            temperature=0.7,
            max_tokens=800
        )

        # Parse JSON response
        import json
        try:
            # Handle potential markdown wrapping
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                response = response.split("```")[1].split("```")[0].strip()
            return json.loads(response)
        except json.JSONDecodeError:
            # Fallback if JSON parsing fails
            return {
                "title": f"Reflections - {datetime.now().strftime('%B %d, %Y')}",
                "date": datetime.now().strftime('%B %d, %Y'),
                "content": response,
                "mood": "reflective",
                "highlights": [],
                "gratitude": [],
                "tomorrow_intention": ""
            }


# Default conversation service instance
conversation_service = ConversationService()