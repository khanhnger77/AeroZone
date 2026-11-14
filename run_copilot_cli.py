"""Interactive CLI tester for Odyssey Copilot."""
import asyncio
import os
import sys

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from odyssey_copilot.backend.router import chat
from odyssey_copilot.backend.schemas import ChatRequest


async def main():
    print("=" * 60)
    print("🚀 MARS ODYSSEY COPILOT - INTERACTIVE MISSION CONSOLE")
    print("Gõ câu hỏi để test (ví dụ: 'Tìm đường an toàn đến Cheyava Falls')")
    print("Gõ 'exit' hoặc 'quit' để thoát.")
    print("=" * 60)
    history = []
    while True:
        try:
            user_input = input("\n[Astronaut] > ")
            if not user_input.strip():
                continue
            if user_input.strip().lower() in ("exit", "quit", "q"):
                print("Ending EVA communications session.")
                break

            req = ChatRequest(message=user_input, history=history)
            res = await chat(req)

            print(f"\n[Capcom] > {res.reply}")
            if res.action:
                print(f"\n[Map Action Triggered]:\n  Type: {res.action.action_type}\n  Description: {res.action.description}\n  Payload: {res.action.payload}")

            history.append({"role": "user", "content": user_input})
            history.append({"role": "model", "content": res.reply})
        except (KeyboardInterrupt, EOFError):
            print("\nSession interrupted.")
            break


if __name__ == "__main__":
    asyncio.run(main())
