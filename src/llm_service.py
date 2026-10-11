from __future__ import annotations

import re

from openai import OpenAI
from google import genai
import groq

from src.settings import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GROQ_API_KEY,
    GROQ_MODEL,
    LLM_PROVIDER_PRIORITY,
    OPENAI_API_KEY,
    OPENAI_MODEL,
)


def context_batches(context: str, limit: int) -> list[str]:
    """Partition at source boundaries without dropping or cutting evidence."""
    if len(context) <= limit:
        return [context]
    groups: dict[str, list[str]] = {}
    for block in re.split(r"\n\n(?=Source:)", context):
        match = re.search(r"^Bank: (.+)$", block, re.MULTILINE)
        groups.setdefault(match.group(1) if match else "Unknown", []).append(block)
    batches = []
    for blocks in groups.values():
        current = ""
        for block in dict.fromkeys(blocks):
            if current and len(current) + len(block) + 2 > limit:
                batches.append(current)
                current = ""
            current = current + "\n\n" + block if current else block
        if current:
            batches.append(current)
    return batches


def groq_completion(client, system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
    """Run one validated Groq completion without exposing provider internals."""
    completion = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=max_tokens,
    )
    content = completion.choices[0].message.content
    if not content:
        raise ValueError("Empty model response")
    if getattr(completion.choices[0], "finish_reason", None) == "length":
        raise ValueError("Incomplete model response")
    return content


def synthesis_prompt(query: str, partial_results: list[str]) -> str:
    """Build a final comparison request from source-preserving partial results."""
    evidence = "\n\n".join(
        f"PARTIAL EVIDENCE {number}:\n{result}"
        for number, result in enumerate(partial_results, start=1)
    )
    return f"""Create the final answer to the original question from all partial evidence below.

Original question:
{query}

Important rules:
- Each partial result covers only a subset of the retrieved sources. A bank absent
  from one partial result may be present in another.
- Ignore any partial statement that another requested bank was not found. Decide
  whether information is missing only after checking every partial result.
- Produce one consolidated answer, not separate answers pasted together.
- For a bank comparison, produce one overall Markdown table with one bank per
  column and comparable tenors in aligned rows.
- Keep standard fixed deposits and special product variants clearly separated.
- Preserve every supported rate, tenor, deposit-amount tier, payment condition,
  source citation, and source conflict. Do not invent or merge unsupported rates.
- A Bank and AYA Bank are different banks. Never infer that one means the other.

{evidence}"""


def generate_answer(
    query: str,
    context: str,
    system_prompt: str,
) -> str:
    prompt = (
        f"Relevant context from the knowledge base:\n{context}\n\n"
        f"Question:\n{query}"
    )
    if not LLM_PROVIDER_PRIORITY:
        return "Retrieval-only mode is enabled. Review the retrieved sources below; no AI answer was generated."

    failures = []
    for provider in LLM_PROVIDER_PRIORITY:
        key = {"groq": GROQ_API_KEY, "gemini": GEMINI_API_KEY, "openai": OPENAI_API_KEY}.get(provider)
        if not key:
            failures.append(f"{provider}: API key is not configured")
            continue
        try:
            if provider == "groq" and GROQ_API_KEY:
                client = groq.Groq(api_key=GROQ_API_KEY)
                # Split requests instead of discarding evidence when a provider
                # cannot accept the complete comparison context.
                for attempt, limit in enumerate((12000, 8000, 5000)):
                    batches = context_batches(context, limit)
                    answers = []
                    try:
                        for batch in batches:
                            batch_instruction = (
                                "This is one part of a larger evidence set. Extract and organize "
                                "only the facts supported by this part, including every rate, tenor, "
                                "amount tier, product variant, payment condition, conflict, and source. "
                                "Do not discuss requested banks absent from this part and do not say "
                                "that their information was not found. A Bank and AYA Bank are distinct."
                                if len(batches) > 1 else ""
                            )
                            batch_prompt = (
                                f"{batch_instruction}\nRelevant context from the knowledge base:\n{batch}\n\n"
                                f"Question:\n{query}"
                            )
                            answers.append(groq_completion(client, system_prompt, batch_prompt))
                        if len(answers) == 1:
                            return answers[0]
                        return groq_completion(
                            client,
                            system_prompt,
                            synthesis_prompt(query, answers),
                            max_tokens=4096,
                        )
                    except Exception as exc:
                        if getattr(exc, "status_code", None) != 413 or attempt == 2:
                            raise

            if provider == "gemini" and GEMINI_API_KEY:
                with genai.Client(api_key=GEMINI_API_KEY) as client:
                    response = client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=f"{system_prompt}\n\n{prompt}",
                    )
                    content = getattr(response, "text", None)
                if content:
                    return content
                failures.append("gemini: empty response")
                continue

            if provider == "openai" and OPENAI_API_KEY:
                client = OpenAI(api_key=OPENAI_API_KEY)
                response = client.responses.create(
                    model=OPENAI_MODEL,
                    input=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                )
                content = getattr(response, "output_text", None)
                if content:
                    return content
                failures.append("openai: empty response")
                continue
        except Exception as exc:
            # Do not expose exception bodies: they may contain credentials or source text.
            status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            status_label = f" (HTTP {status})" if isinstance(status, int) else ""
            failures.append(f"{provider}: {type(exc).__name__}{status_label}")
            if status == 413:
                failures[-1] += " — Request too large; reduce retrieved context or use a model with higher limits."
            elif status == 404:
                failures[-1] += f" — Check {provider.upper()}_MODEL against models available to your API key."
            continue

    return (
        "Unable to generate an AI answer because no configured provider succeeded. "
        "Check your API keys, model availability, quota, and network connection, then restart the app. "
        "Retrieved sources are shown below.\n\n" + "\n".join(f"- {failure}" for failure in failures)
    )
