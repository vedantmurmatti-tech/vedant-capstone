/**
 * Static UI copy — example prompts shown as suggestion chips on the
 * dashboard command console and the empty chat state. These are not
 * fetched from the backend and are not domain data; there's no AI
 * endpoint to send them to yet (see ChatUnavailableError in api.ts).
 */

export const commandChips: string[] = [
  "Scan my upcoming deadlines",
  "Explain my Business Analytics notes",
  "Summarize this week's coursework",
  "Help me prepare for a quiz",
];

export const suggestedPrompts = commandChips;
