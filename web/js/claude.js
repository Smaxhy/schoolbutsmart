// Talking to Claude from the phone: "Maak deze taak" when the build hasn't prepared an answer,
// and your own follow-up prompts per task. Runs in the browser with your own API key (stored
// only on this device), so the public site never holds a key. The SDK is loaded on first use.

const SDK_URL = "https://cdn.jsdelivr.net/npm/@anthropic-ai/sdk@0.127.0/+esm";
// Haiku 5.5 first; accounts without access to it (HTTP 404) fall back to Haiku 4.5.
const MODELS = ["claude-haiku-5-5", "claude-haiku-4-5"];

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

export class ClaudeError extends Error {
  constructor(message, { needsWorkspace = false } = {}) {
    super(message);
    this.needsWorkspace = needsWorkspace;
  }
}

// Anthropic's own explanation, e.g. "Your credit balance is too low to access the Anthropic API."
function apiDetail(err) {
  const body = err && err.error;
  return (body && body.error && body.error.message) || (body && body.message) || "";
}

function friendlyError(err) {
  if (err instanceof ClaudeError) return err;
  const status = err && err.status;
  const detail = apiDetail(err);
  if (status === 401) return new ClaudeError("Je API-sleutel klopt niet. Plak hem opnieuw.");
  if (status === 403) return new ClaudeError("Deze API-sleutel heeft geen toegang. Controleer je Anthropic-account.");
  if (status === 429) return new ClaudeError("Te veel aanvragen of je tegoed is op. Probeer het straks opnieuw.");
  if (status === 400 && /workspace/i.test(detail)) {
    return new ClaudeError("Je API-sleutel hoort niet bij een workspace. Vul hieronder je workspace-ID in, of gebruik een sleutel uit een workspace.", { needsWorkspace: true });
  }
  if (/credit/i.test(detail || (err && err.message) || "")) {
    return new ClaudeError("Je Anthropic-tegoed is op. Vul het aan op console.anthropic.com → Billing.");
  }
  if (status >= 500) return new ClaudeError("Anthropic heeft even een probleem. Probeer het zo opnieuw.");
  if (status) return new ClaudeError(`Er ging iets mis (${status})${detail ? `: ${detail}` : "."}`);
  return new ClaudeError("Geen verbinding met Claude. Ben je online?");
}

/**
 * Stream one answer for a conversation ([{role, content}], ending with a user turn).
 * Calls onText(fullTextSoFar) as text arrives; resolves with the final text.
 */
export async function streamChat({ apiKey, workspaceId, system, messages, onText, signal }) {
  try {
    const { default: Anthropic } = await loadSdk();
    const client = new Anthropic({
      apiKey,
      dangerouslyAllowBrowser: true,
      maxRetries: 1,
      // Needed for API keys that aren't scoped to a workspace (Anthropic says so in a 400).
      defaultHeaders: workspaceId ? { "anthropic-workspace-id": workspaceId } : undefined,
    });

    for (const model of MODELS) {
      const params = { model, max_tokens: 16000, system: system || FALLBACK_SYSTEM_PROMPT, messages };
      if (model === "claude-haiku-5-5") params.output_config = { effort: "medium" };

      let text = "";
      const stream = client.messages.stream(params, { signal });
      stream.on("text", (delta) => {
        text += delta;
        onText(text);
      });
      stream.on("error", () => {}); // handled through finalMessage() below

      let final;
      try {
        final = await stream.finalMessage();
      } catch (err) {
        // Model not available on this account: try the next Haiku.
        if (err && err.status === 404 && model !== MODELS[MODELS.length - 1]) continue;
        throw err;
      }
      if (final.stop_reason === "refusal") throw new ClaudeError("Claude wilde hier niet op antwoorden.");
      if (final.stop_reason === "max_tokens") text += "\n\n(Afgebroken: het antwoord was te lang. Vraag om het vervolg.)";
      if (!text.trim()) throw new ClaudeError("Claude gaf geen antwoord. Probeer het opnieuw.");
      return text;
    }
    throw new ClaudeError("Geen Claude-model beschikbaar voor deze sleutel.");
  } catch (err) {
    if (signal && signal.aborted) throw new ClaudeError("Gestopt.");
    throw friendlyError(err);
  }
}
