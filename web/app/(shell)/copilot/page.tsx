"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";

import { Icon } from "@/components/Icon";
import { queryCopilot } from "@/lib/api";
import { COPILOT_SUGGESTIONS, SESSION_STORAGE, SOURCE_LABELS } from "@/lib/constants";
import { useFilters } from "@/lib/filters";
import { formatInteger, sourceLabel } from "@/lib/format";
import { ingestedSourceRows, operatorUnavailable } from "@/lib/sources";
import { useOverviewQuery } from "@/lib/hooks";
import type { Citation, CopilotTurnResponse } from "@/lib/types";
import { useDrawerSeed } from "@/components/DrawerSeed";
import { cn } from "@/lib/cn";

type ChatTurn = {
  id: string;
  question: string;
  pending?: boolean;
  response?: CopilotTurnResponse;
  error?: string;
};

export default function CopilotPage() {
  const { filters, openDrawer } = useFilters();
  const { setSeed } = useDrawerSeed();
  const overview = useOverviewQuery(filters);
  const [question, setQuestion] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [retrieving, setRetrieving] = useState(false);
  const sendingRef = useRef(false);
  const sessionRef = useRef<string | null>(null);
  const filtersRef = useRef(filters);
  const scroller = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  sessionRef.current = sessionId;
  filtersRef.current = filters;

  useEffect(() => {
    const stored = window.sessionStorage.getItem(SESSION_STORAGE);
    if (stored) {
      setSessionId(stored);
      sessionRef.current = stored;
    }
  }, []);

  useEffect(() => {
    const node = scroller.current;
    if (!node) return;
    if (!turns.length) {
      node.scrollTop = 0;
      return;
    }
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
  }, [turns, retrieving]);

  function resizeInput() {
    const node = inputRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 160)}px`;
  }

  const send = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || sendingRef.current) return;
    sendingRef.current = true;
    setQuestion("");
    if (inputRef.current) inputRef.current.style.height = "auto";
    setRetrieving(true);
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setTurns((current) => [...current, { id, question: trimmed, pending: true }]);
    try {
      const response = await queryCopilot(trimmed, filtersRef.current, sessionRef.current);
      window.sessionStorage.setItem(SESSION_STORAGE, response.session_id);
      setSessionId(response.session_id);
      sessionRef.current = response.session_id;
      setTurns((current) =>
        current.map((turn) => (turn.id === id ? { id, question: trimmed, response } : turn)),
      );
    } catch (error) {
      setTurns((current) =>
        current.map((turn) =>
          turn.id === id
            ? {
                id,
                question: trimmed,
                error: error instanceof Error ? error.message : "Copilot failed",
              }
            : turn,
        ),
      );
    } finally {
      sendingRef.current = false;
      setRetrieving(false);
      inputRef.current?.focus();
    }
  }, []);

  function activateSend(
    event: { preventDefault(): void; stopPropagation(): void; button?: number },
    text: string,
  ) {
    if (event.button != null && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    void send(text);
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void send(question);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void send(question);
  }

  function newChat() {
    setTurns([]);
    setSessionId(null);
    window.sessionStorage.removeItem(SESSION_STORAGE);
    setQuestion("");
    inputRef.current?.focus();
  }

  const filterChips = Object.entries(filters).filter(([, value]) => Boolean(value));
  const sources = ingestedSourceRows(overview.data?.counts_by_source);

  return (
    <div className="copilot-shell flex h-full min-h-0 min-w-0 flex-col">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-outline-variant/70 bg-surface-container-lowest/90 px-4 py-3 backdrop-blur md:px-6">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-on-primary shadow-lift">
              <Icon name="smart_toy" className="text-[18px]" />
            </span>
            <div className="min-w-0">
              <h1 className="font-headline-md text-[18px] leading-6 text-on-surface">Copilot</h1>
              <p className="truncate font-label-md text-[12px] text-on-surface-variant">
                Answers from public reviews, like a chat
              </p>
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={newChat}
          className="inline-flex items-center gap-1.5 rounded-full border border-outline-variant bg-surface px-3 py-1.5 font-label-md text-label-md text-on-surface hover:bg-surface-container"
        >
          <Icon name="edit_square" className="text-[16px]" />
          New chat
        </button>
      </header>

      <div ref={scroller} className="copilot-scroll min-h-0 flex-1 overflow-y-auto">
        <div
          className={cn(
            "mx-auto w-full max-w-3xl px-4 py-6 md:px-6 md:py-8",
            !turns.length && "flex min-h-full flex-col",
          )}
        >
          {!turns.length ? (
            <EmptyState onPick={activateSend} />
          ) : (
            <div className="space-y-8">
              {turns.map((turn) => (
                <article key={turn.id} className="space-y-4">
                  <div className="flex justify-end">
                    <div className="max-w-[min(100%,34rem)] rounded-3xl rounded-br-md bg-primary px-4 py-3 text-on-primary shadow-lift">
                      <p className="font-body-lg text-[15px] leading-6">{turn.question}</p>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-secondary-container text-primary">
                      <Icon name="auto_awesome" className="text-[18px]" />
                    </div>
                    <div className="min-w-0 flex-1 pt-0.5">
                      {turn.pending ? <TypingIndicator /> : null}
                      {turn.error ? (
                        <div className="rounded-2xl border border-dashed border-error bg-error-container/40 px-4 py-3 font-body-md text-error">
                          {turn.error}
                        </div>
                      ) : null}
                      {turn.response ? (
                        <CopilotAnswer
                          turn={turn.response}
                          onCite={(citation) => {
                            setSeed(citation);
                            openDrawer(citation.document_id, citation.chunk_id);
                          }}
                        />
                      ) : null}
                    </div>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="shrink-0 border-t border-outline-variant/70 bg-surface-container-lowest/95 px-4 pb-4 pt-3 backdrop-blur md:px-6">
        <div className="mx-auto w-full max-w-3xl">
          {turns.length ? (
            <div className="mb-3 flex gap-2 overflow-x-auto copilot-scroll pb-1">
              {COPILOT_SUGGESTIONS.map((item) => (
                <button
                  key={item.title}
                  type="button"
                  disabled={retrieving}
                  title={item.question}
                  onPointerDown={(event) => activateSend(event, item.question)}
                  onClick={(event) => activateSend(event, item.question)}
                  className="shrink-0 cursor-pointer touch-manipulation rounded-full border border-outline-variant bg-surface px-3 py-1.5 font-label-md text-[12px] text-on-surface hover:border-primary hover:bg-primary/5 disabled:opacity-50"
                >
                  {item.title}
                </button>
              ))}
            </div>
          ) : null}
          <form
            onSubmit={onSubmit}
            className="flex items-end gap-2 rounded-3xl border border-outline-variant bg-surface px-3 py-2 shadow-lift focus-within:border-primary"
          >
            <textarea
              ref={inputRef}
              rows={1}
              value={question}
              onChange={(event) => {
                setQuestion(event.target.value);
                resizeInput();
              }}
              onKeyDown={onKeyDown}
              enterKeyHint="send"
              placeholder="Ask anything about wishlist behaviour…"
              className="max-h-40 min-h-[44px] w-full resize-none bg-transparent px-2 py-2.5 font-body-lg text-[15px] leading-6 text-on-surface placeholder:text-on-surface-variant/70 focus:outline-none"
            />
            <button
              type="submit"
              disabled={retrieving || !question.trim()}
              className="mb-1 flex h-10 w-10 shrink-0 touch-manipulation items-center justify-center rounded-full bg-primary text-on-primary disabled:opacity-40"
              aria-label="Send"
            >
              <Icon name="send" className="text-[18px]" />
            </button>
          </form>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 px-1">
            <p className="font-label-md text-[11px] text-on-surface-variant">
              Enter to send · Shift+Enter for a new line
            </p>
            <p className="font-label-md text-[11px] text-on-surface-variant">
              {sources.length
                ? `${formatInteger(sources.reduce((sum, row) => sum + row.eligible_count, 0))} public reviews`
                : "Public reviews only"}
              {filterChips.length ? ` · ${filterChips.length} filter${filterChips.length > 1 ? "s" : ""}` : ""}
              {operatorUnavailable(overview.data?.unavailable_sources).length
                ? " · store ingest issue"
                : ""}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function EmptyState({
  onPick,
}: {
  onPick: (
    event: { preventDefault(): void; stopPropagation(): void; button?: number },
    question: string,
  ) => void;
}) {
  return (
    <div className="flex flex-1 flex-col justify-center py-4">
      <div className="mb-8 text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-primary text-on-primary shadow-lift">
          <Icon name="smart_toy" className="text-[28px]" />
        </div>
        <h2 className="font-headline-lg text-headline-lg text-on-surface">How can I help?</h2>
        <p className="mx-auto mt-2 max-w-md font-body-md text-[15px] leading-6 text-on-surface-variant">
          Ask in plain language. I read public Myntra reviews and answer like a chat — with
          supporting comments underneath.
        </p>
      </div>
      <p className="mb-3 font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">
        Suggested questions
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {COPILOT_SUGGESTIONS.map((item) => (
          <button
            key={item.title}
            type="button"
            onPointerDown={(event) => onPick(event, item.question)}
            onClick={(event) => onPick(event, item.question)}
            className="group cursor-pointer touch-manipulation rounded-2xl border border-outline-variant bg-surface-container-lowest p-4 text-left shadow-lift hover:border-primary hover:bg-white"
          >
            <div className="flex items-start justify-between gap-2">
              <span className="font-headline-md text-[15px] leading-5 text-on-surface">{item.title}</span>
              <Icon
                name="arrow_outward"
                className="text-[16px] text-on-surface-variant group-hover:text-primary"
              />
            </div>
            <p className="mt-1.5 font-body-md text-[13px] leading-5 text-on-surface-variant">{item.hint}</p>
          </button>
        ))}
      </div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="inline-flex items-center gap-1 rounded-2xl bg-surface-container px-4 py-3">
      <span className="copilot-dot h-2 w-2 rounded-full bg-primary" />
      <span className="copilot-dot h-2 w-2 rounded-full bg-primary" />
      <span className="copilot-dot h-2 w-2 rounded-full bg-primary" />
    </div>
  );
}

function splitAnswer(answer: string): { lead: string; quotes: string[] } {
  const quotes: string[] = [];
  const lead: string[] = [];
  for (const raw of (answer || "").split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const quoted = line.match(/^"([\s\S]*)"$/);
    if (quoted?.[1]) quotes.push(quoted[1]);
    else lead.push(line);
  }
  return { lead: lead.join(" ").trim(), quotes };
}

function CopilotAnswer({
  turn,
  onCite,
}: {
  turn: CopilotTurnResponse;
  onCite: (citation: Citation) => void;
}) {
  const failed = turn.status === "error" || turn.status === "failed_grounding";
  const declined = ["declined", "refused"].includes(turn.status);
  const { lead, quotes } = splitAnswer(turn.answer || "");
  const reviews =
    turn.citations.length > 0
      ? turn.citations.slice(0, 2).map((citation) => ({
          quote: citation.quote,
          source: citation.source_type,
          citation,
        }))
      : quotes.map((quote) => ({ quote, source: undefined as string | undefined, citation: undefined }));

  if (failed) {
    return (
      <div className="rounded-2xl border border-dashed border-error bg-error-container/30 px-4 py-3">
        <p className="font-headline-md text-[16px] text-error">Could not complete this turn</p>
        <p className="mt-1 font-body-md text-on-surface-variant">
          {turn.answer || turn.error || "Try asking again."}
        </p>
      </div>
    );
  }
  if (declined) {
    return (
      <div className="rounded-2xl border border-dashed border-outline-variant bg-surface-container px-4 py-3">
        <p className="font-headline-md text-[16px] text-on-surface">
          {turn.status === "refused" ? "Out of scope" : "Not enough evidence"}
        </p>
        <p className="mt-1 font-body-md leading-6 text-on-surface">{turn.answer || turn.error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="max-w-prose font-body-lg text-[16px] leading-7 text-on-surface">{lead || turn.answer}</p>
      {reviews.length ? (
        <div>
          <p className="mb-2 font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">
            Supporting reviews
          </p>
          <div className="space-y-2">
            {reviews.map((review, index) => (
              <button
                key={`${review.quote.slice(0, 24)}:${index}`}
                type="button"
                disabled={!review.citation}
                onClick={() => review.citation && onCite(review.citation)}
                className={cn(
                  "block w-full rounded-2xl border border-outline-variant bg-white px-4 py-3 text-left",
                  review.citation && "hover:border-primary hover:bg-primary/5",
                )}
              >
                <p className="font-body-md text-[14px] leading-6 text-on-surface">“{review.quote}”</p>
                {review.source ? (
                  <span className="mt-2 inline-flex items-center gap-1 font-label-md text-[11px] text-on-surface-variant">
                    <Icon name="format_quote" className="text-[14px]" />
                    {sourceLabel(review.source, SOURCE_LABELS)}
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
