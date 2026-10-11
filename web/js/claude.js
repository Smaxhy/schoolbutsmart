// Talking to Claude from the phone: "Maak deze taak" when the build hasn't prepared an answer,
// and your own follow-up prompts per task. Runs in the browser with your own API key (stored
// only on this device), so the public site never holds a key. The SDK is loaded on first use.

const SDK_URL = "https://cdn.jsdelivr.net/npm/@anthropic-ai/sdk@0.127.0/+esm";
const MODEL = "claude-haiku-5-5";

// Normally the build sends the prompt it uses itself (tasks.json "ai_system_prompt").
const FALLBACK_SYSTEM_PROMPT = `Je bent de assistent van een student van Artevelde Hogeschool en maakt de opdracht uit Canvas zo volledig mogelijk.
Antwoord in het Nederlands met de kopjes "## Wat wordt er gevraagd", "## Klaar om in te dienen" (alles wat in tekst kan, volledig af),
"## Zo maak je de rest" (exacte stappen in de juiste software voor beeld, audio of video) en "## Checklist voor het indienen".
Vraagt de student daarna iets, voer het uit en geef het volledige aangepaste resultaat. Gebruik alleen "## ", "- ", "1. " en **vet**.`;

let sdkPromise = null;

function loadSdk() {
  if (!sdkPromise) {
    sdkPromise = import(SDK_URL).catch((err) => {
      sdkPromise = null; // allow a retry after a network hiccup
      throw err;
    });
  }
  return sdkPromise;
}

/** The first user message: everything Claude needs to know about the task. */
export function taskPrompt(task, today, dueText) {
  const lines = [
    `Vandaag: ${today}`,
    `Vak: ${task.course || "onbekend"}`,
    `Opdracht: ${task.title}`,
    `Deadline: ${dueText || task.due_date}`,
  ];
  if (task.weight != null) lines.push(`Gewicht: ${task.weight}% van het eindcijfer`);
  lines.push("", "Beschrijving uit Canvas:", "<beschrijving>", task.description || "(geen beschrijving)", "</beschrijving>");
  return lines.join("\n");
}

export class ClaudeError extends Error {}

function friendlyError(err) {
  if (err instanceof ClaudeError) return err;
  const status = err && err.status;
  if (status === 401) return new ClaudeError("Je API-sleutel klopt niet. Plak hem opnieuw.");
  if (status === 403) return new ClaudeError("Deze API-sleutel heeft geen toegang. Controleer je Anthropic-account.");
  if (status === 429) return new ClaudeError("Te veel aanvragen of je tegoed is op. Probeer het straks opnieuw.");
  if (status === 400 && /credit/i.test(err.message || "")) {
    return new ClaudeError("Je Anthropic-tegoed is op. Vul het aan op console.anthropic.com.");
  }
  if (status >= 500) return new ClaudeError("Anthropic heeft even een probleem. Probeer het zo opnieuw.");
  if (status) return new ClaudeError(`Er ging iets mis (${status}).`);
  return new ClaudeError("Geen verbinding met Claude. Ben je online?");
}

/**
 * Stream one answer for a conversation ([{role, content}], ending with a user turn).
 * Calls onText(fullTextSoFar) as text arrives; resolves with the final text.
 */
export async function streamChat({ apiKey, system, messages, onText, signal }) {
  try {
    const { default: Anthropic } = await loadSdk();
    const client = new Anthropic({ apiKey, dangerouslyAllowBrowser: true, maxRetries: 1 });
    const stream = client.messages.stream(
      {
        model: MODEL,
        max_tokens: 16000,
        output_config: { effort: "medium" },
        system: system || FALLBACK_SYSTEM_PROMPT,
        messages,
      },
      { signal },
    );

    let text = "";
    for await (const event of stream) {
      if (event.type === "content_block_delta" && event.delta.type === "text_delta") {
        text += event.delta.text;
        onText(text);
      }
    }

    const final = await stream.finalMessage();
    if (final.stop_reason === "refusal") throw new ClaudeError("Claude wilde hier niet op antwoorden.");
    if (final.stop_reason === "max_tokens") text += "\n\n(Afgebroken: het antwoord was te lang. Vraag om het vervolg.)";
    if (!text.trim()) throw new ClaudeError("Claude gaf geen antwoord. Probeer het opnieuw.");
    return text;
  } catch (err) {
    if (signal && signal.aborted) throw new ClaudeError("Gestopt.");
    throw friendlyError(err);
  }
}
