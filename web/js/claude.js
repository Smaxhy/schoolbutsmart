// "Plan met Claude": asks Claude for a study plan for one assignment.
// Runs entirely in the browser with the user's own API key (stored only on this device),
// so the public GitHub Pages site never holds a key. The SDK is loaded on first use.

const SDK_URL = "https://cdn.jsdelivr.net/npm/@anthropic-ai/sdk@0.127.0/+esm";
const MODEL = "claude-opus-5-5";

const SYSTEM_PROMPT = `Je bent een studiecoach voor een student aan Artevelde Hogeschool (Gent).
Je krijgt één opdracht uit Canvas en helpt de student die zelf uit te voeren: je schrijft de opdracht niet voor hen.

Antwoord in het Nederlands, kort en concreet (maximaal ongeveer 350 woorden), met precies deze kopjes:
## Wat wordt er gevraagd
2 tot 4 zinnen in gewone taal.
## Stappenplan
Genummerde stappen. Geef bij elke stap een geschatte tijd en een streefdatum vóór de deadline (reken vanaf vandaag).
## Checklist voor het indienen
## Zo begin je
Eén concrete eerste stap van ongeveer 15 minuten, en waar nuttig een mogelijke structuur of opbouw.
## Vragen voor je docent
Alleen als iets in de opdracht echt onduidelijk is; laat dit kopje anders weg.

Regels:
- Baseer je alleen op de gegeven informatie. Verzin geen eisen, puntenverdeling of bronnen. Is de beschrijving mager, zeg dat dan en raad aan de opdracht in Canvas na te lezen.
- Schrijf geen kant-en-klare tekst, code of antwoorden om in te dienen.
- Gebruik alleen "## " voor kopjes, "- " of "1. " voor lijsten en **vet** voor nadruk. Geen tabellen.`;

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
export async function streamPlan(task, apiKey, { onText, signal, today }) {
  try {
    const { default: Anthropic } = await loadSdk();
    const client = new Anthropic({ apiKey, dangerouslyAllowBrowser: true, maxRetries: 1 });

    const stream = client.beta.messages.stream(
      {
        model: MODEL,
        max_tokens: 16000,
        output_config: { effort: "medium" },
        betas: ["server-side-fallback-2026-07-01"],
        fallbacks: "default",
        system: SYSTEM_PROMPT,
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
