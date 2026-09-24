import type { ChatHistoryTurn } from "@/types";

/**
 * The one shared conversation identity/history both `Dashboard.tsx` (the
 * Orb) and `Chat.tsx` (Conversations) read from and write to — not a
 * second conversation-memory system alongside Phase 4's `conversationId`/
 * `history` mechanism, but a single module-level holder for the exact
 * same two values, so they survive navigating between the two pages
 * (each is its own React Router route component, mounted/unmounted on
 * every navigation — component-local `useRef` state, which is what
 * `Dashboard.tsx` used before this existed, does NOT survive that).
 *
 * A real page reload/refresh re-initializes this module from scratch —
 * that is the one intentional session boundary. There is no other
 * "start fresh" affordance yet beyond that and `Chat.tsx`'s own existing
 * "New conversation" button (wired to `resetVoiceConversation()`).
 */

let conversationId = crypto.randomUUID();
let history: ChatHistoryTurn[] = [];

export function getVoiceConversation(): { conversationId: string; history: ChatHistoryTurn[] } {
  return { conversationId, history };
}

/** Appends one real, completed user/assistant exchange, trimmed to `maxMessages`. */
export function appendVoiceTurn(userMessage: string, assistantReply: string, maxMessages: number): void {
  history = [
    ...history,
    { role: "user" as const, content: userMessage },
    { role: "assistant" as const, content: assistantReply },
  ].slice(-maxMessages);
}

/** Starts a genuinely new logical conversation — a fresh id, empty history. */
export function resetVoiceConversation(): void {
  conversationId = crypto.randomUUID();
  history = [];
}
