// "Hulp van Claude" on the phone itself (only used when the build hasn't prepared an answer).
// Runs entirely in the browser with the user's own API key (stored only on this device),
// so the public GitHub Pages site never holds a key. The SDK is loaded on first use.

const SDK_URL = "https://cdn.jsdelivr.net/npm/@anthropic-ai/sdk@0.127.0/+esm";
const MODEL = "claude-haiku-5-5";

// Normally the build sends the same prompt it uses itself (tasks.json "ai_system_prompt").
const FALLBACK_SYSTEM_PROMPT = `Je helpt een student van Artevelde Hogeschool met één opdracht uit Canvas.
Antwoord in het Nederlands met de kopjes "## Wat wordt er gevraagd", "## Stappenplan" (met tijd en streefdatum per stap),
"## Uitwerking" (een eerste versie van tekstdelen, of uitgewerkte concepten en concrete stappen in de software voor beeld/audio/video)
en "## Checklist voor het indienen". Verzin geen eisen. Gebruik alleen "## ", "- ", "1. " en **vet**.`;

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

function buildPrompt(task, today) {
  const lines = [
    `Vandaag: ${today}`,
    `Vak: ${task.course || "onbekend"}`,
    `Opdracht: ${task.title}`,
    `Deadline: ${task.due_date}`,
  ];
  if (task.weight != null) lines.push(`Gewicht: ${task.weight}% van het eindcijfer`);
  lines.push("", "Beschrijving uit Canvas:", "<beschrijving>", task.description || "(geen beschrijving)", "</beschrijving>");
  return lines.join("\n");
}

export class PlanError extends Error {}

function friendlyError(err) {
  if (err instanceof PlanError) return err;
  const status = err && err.status;
  if (status === 401) return new PlanError("Je API-sleutel klopt niet. Controleer hem in de instellingen.");
  if (status === 403) return new PlanError("Deze API-sleutel heeft geen toegang. Controleer je Anthropic-account.");
  if (status === 429) return new PlanError("Te veel aanvragen of je tegoed is op. Probeer het straks opnieuw.");
  if (status === 400 && /credit/i.test(err.message || "")) {
    return new PlanError("Je Anthropic-tegoed is op. Vul het aan op console.anthropic.com.");
  }
  if (status >= 500) return new PlanError("Anthropic heeft even een probleem. Probeer het zo opnieuw.");
  if (status) return new PlanError(`Er ging iets mis (${status}).`);
  return new PlanError("Geen verbinding met Claude. Ben je online?");
}

/**
 * Stream a study plan. Calls onText(fullTextSoFar) as text arrives; resolves with the final text.
 */
export async function streamPlan(task, apiKey, { onText, signal, today, system }) {
  try {
    const { default: Anthropic } = await loadSdk();
    const client = new Anthropic({ apiKey, dangerouslyAllowBrowser: true, maxRetries: 1 });

    const stream = client.messages.stream(
      {
        model: MODEL,
        max_tokens: 8000,
        output_config: { effort: "medium" },
        system: system || FALLBACK_SYSTEM_PROMPT,
        messages: [{ role: "user", content: buildPrompt(task, today) }],
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
    if (final.stop_reason === "refusal") {
      throw new PlanError("Claude kon voor deze opdracht geen plan maken.");
    }
    if (!text.trim()) throw new PlanError("Claude gaf geen antwoord. Probeer het opnieuw.");
    return text;
  } catch (err) {
    if (signal && signal.aborted) throw new PlanError("Gestopt.");
    throw friendlyError(err);
  }
}
