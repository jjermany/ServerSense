import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  Bell,
  Bot,
  ChevronDown,
  Clock3,
  Pencil,
  RotateCcw,
  Search,
  Send,
  Server,
  Sparkles,
  Square,
  Trash2,
  User,
  X,
} from "lucide-react";
import { PageHeader } from "../components/UI";
import { api } from "../api";
import { formatDate } from "../timeFormat";
import { useTimeZone } from "../timeZoneContext";

type Message = {
  id?: number;
  role: "user" | "assistant";
  content: string;
  tools?: string[];
  model?: string | null;
  provider?: string | null;
  source?: "user" | "serversense" | "sense_ai";
};
type Conversation = { id: number; title: string; updated_at: string; summary?: string };
type Job = {
  id: string;
  conversation_id: number;
  user_message_id?: number;
  status: string;
  model: string;
  partial_response: string;
  queue_position?: number | null;
  backgrounded: boolean;
  notify_on_completion: boolean;
  queue_wait_seconds?: number | null;
  time_to_first_token_seconds?: number | null;
  inference_seconds?: number | null;
  generated_tokens?: number | null;
  first_token_at?: string | null;
  error?: string | null;
};
type Notice = {
  id: number;
  title: string;
  preview: string;
  conversation_id?: number;
  read_at?: string | null;
};

const prompts = [
  "How long until I run out of storage?",
  "Which disk is hottest?",
  "Are all my Docker containers healthy?",
  "What changed on my server today?",
];
const terminal = new Set(["completed", "failed", "cancelled", "timed_out", "interrupted"]);

export default function SensePage() {
  const { timeZone } = useTimeZone();
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversation, setConversation] = useState<number>();
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [activity, setActivity] = useState("Checking server telemetry…");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [unread, setUnread] = useState(0);
  const [showNotices, setShowNotices] = useState(false);
  const [search, setSearch] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const [longRunningJob, setLongRunningJob] = useState<string>();
  const [activeStreamJob, setActiveStreamJob] = useState<string>();
  const [foregroundNotify, setForegroundNotify] = useState(true);
  const [quickTelemetryError, setQuickTelemetryError] = useState("");
  const [retryingJobs, setRetryingJobs] = useState<Set<string>>(() => new Set());
  const controllerRef = useRef<AbortController | undefined>(undefined);
  const requestIdRef = useRef<string | undefined>(undefined);
  const stoppedRef = useRef(false);

  const loadConversations = useCallback(
    () =>
      api<Conversation[]>(`/api/ai/conversations${search ? `?q=${encodeURIComponent(search)}` : ""}`).then(
        setConversations,
      ),
    [search],
  );
  const loadConversation = useCallback(async (id: number) => {
    const result = await api<{ id: number; messages: Message[] }>(`/api/ai/conversations/${id}`);
    setConversation(result.id);
    setMessages(result.messages);
  }, []);
  const loadBackgroundState = useCallback(async () => {
    const [jobRows, notificationRows] = await Promise.all([
      api<Job[]>("/api/ai/jobs"),
      api<{ unread: number; items: Notice[] }>("/api/ai/notifications"),
    ]);
    setJobs(jobRows ?? []);
    setNotices(notificationRows?.items ?? []);
    setUnread(notificationRows?.unread ?? 0);
  }, []);

  useEffect(() => void loadConversations(), [loadConversations]);
  useEffect(() => {
    void loadBackgroundState();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void loadBackgroundState();
        void loadConversations();
      }
    }, 3000);
    const refreshVisible = () => {
      if (document.visibilityState === "visible") void loadBackgroundState();
    };
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [loadBackgroundState, loadConversations]);

  const openConversation = async (id: number) => {
    await loadConversation(id);
    setShowHistory(false);
    setShowNotices(false);
  };
  const deleteConversation = async (item: Conversation) => {
    if (!window.confirm(`Remove "${item.title}" and all of its messages?`)) return;
    await api<void>(`/api/ai/conversations/${item.id}`, { method: "DELETE" });
    if (conversation === item.id) {
      setConversation(undefined);
      setMessages([]);
    }
    await Promise.all([loadConversations(), loadBackgroundState()]);
  };
  const renameConversation = async (item: Conversation) => {
    const title = window.prompt("Conversation name", item.title)?.trim();
    if (!title || title === item.title) return;
    await api(`/api/ai/conversations/${item.id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
    await loadConversations();
  };

  const ask = async (text: string) => {
    if (!text.trim() || busy) return;
    setMessages((current) => [...current, { role: "user", content: text, source: "user" }]);
    setBusy(true);
    setDraft("");
    stoppedRef.current = false;
    requestIdRef.current = undefined;
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const response = await fetch("/api/ai/chat/stream", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, conversation_id: conversation }),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail ?? `SENSE request failed (${response.status})`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          const event = block.match(/^event: (.+)$/m)?.[1];
          const raw = block.match(/^data: (.+)$/m)?.[1];
          if (!raw) continue;
          const data = JSON.parse(raw) as {
            message: string;
            conversation_id?: number;
            tools_used?: string[];
            model?: string;
            source?: "serversense" | "sense_ai";
            request_id?: string;
            job_id?: string;
            status?: string;
            queue_position?: number;
            notify_on_completion?: boolean;
            error?: string;
          };
          if (data.request_id) {
            requestIdRef.current = data.request_id;
            setActiveStreamJob(data.request_id);
          }
          if (data.conversation_id) setConversation(data.conversation_id);
          if (event === "activity") setActivity(data.message);
          if (event === "status") {
            setActivity(
              data.status === "queued"
                ? `Queued${data.queue_position ? ` (#${data.queue_position})` : ""}…`
                : data.status === "gathering_context"
                  ? "Gathering relevant telemetry…"
                  : data.status === "analyzing"
                    ? "SENSE AI is analyzing…"
                    : "SENSE AI is responding…",
            );
          }
          if (event === "delta") setDraft((current) => current + data.message);
          if (event === "reset") setDraft("");
          if (event === "backgrounded") {
            setActivity(data.message);
            if (data.job_id) setLongRunningJob(data.job_id);
            else if (requestIdRef.current) setLongRunningJob(requestIdRef.current);
            setForegroundNotify(data.notify_on_completion ?? true);
            void loadBackgroundState();
          }
          if (event === "error") throw new Error(data.message);
          if (event === "message") {
            setLongRunningJob(undefined);
            setActiveStreamJob(undefined);
            setDraft("");
            setMessages((current) => [
              ...current,
              {
                role: "assistant",
                content: data.message,
                tools: data.tools_used,
                model: data.model,
                source: data.source,
              },
            ]);
          }
          if (event === "terminal") {
            setLongRunningJob(undefined);
            setActiveStreamJob(undefined);
            setDraft("");
            setMessages((current) => [
              ...current,
              {
                role: "assistant",
                content: data.message || data.error || `SENSE job was ${data.status}.`,
                model: data.model,
                source: "sense_ai",
              },
            ]);
          }
        }
        if (done) break;
      }
    } catch (error) {
      if (controller.signal.aborted || stoppedRef.current) return;
      setDraft("");
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          source: "serversense",
          content: `I couldn't complete that request: ${error instanceof Error ? error.message : "Unknown error"}`,
        },
      ]);
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = undefined;
        requestIdRef.current = undefined;
        setActiveStreamJob(undefined);
        setBusy(false);
      }
      void Promise.all([loadConversations(), loadBackgroundState()]);
    }
  };
  const stop = () => {
    if (!busy) return;
    stoppedRef.current = true;
    if (requestIdRef.current) {
      void fetch(`/api/ai/requests/${requestIdRef.current}`, {
        method: "DELETE",
        credentials: "include",
      });
    }
    controllerRef.current?.abort();
    setLongRunningJob(undefined);
    setActiveStreamJob(undefined);
    setDraft("");
    setBusy(false);
    setMessages((current) => [
      ...current,
      { role: "assistant", source: "serversense", content: "Request stopped." },
    ]);
  };
  const cancelJob = async (job: Job) => {
    await api(`/api/ai/requests/${job.id}`, { method: "DELETE" });
    await loadBackgroundState();
  };
  const retryJob = async (job: Job) => {
    setRetryingJobs((current) => new Set(current).add(job.id));
    try {
      const retried = await api<Job>(`/api/ai/jobs/${job.id}/retry`, { method: "POST" });
      setJobs((current) => [retried, ...current.filter((item) => item.id !== retried.id)]);
      await loadBackgroundState();
    } finally {
      setRetryingJobs((current) => {
        const updated = new Set(current);
        updated.delete(job.id);
        return updated;
      });
    }
  };
  const setJobNotification = async (jobId: string, enabled: boolean) => {
    const updated = await api<Job>(`/api/ai/jobs/${jobId}/notification`, {
      method: "PATCH",
      body: JSON.stringify({ notify_on_completion: enabled }),
    });
    setJobs((current) => current.map((job) => (job.id === jobId ? updated : job)));
    if (jobId === longRunningJob) setForegroundNotify(enabled);
  };
  const askQuickTelemetry = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const question = new FormData(form).get("quick_message")?.toString().trim() ?? "";
    if (!question) return;
    setQuickTelemetryError("");
    try {
      const result = await api<{
        conversation_id: number;
        message: string;
        tools_used: string[];
      }>("/api/ai/direct", {
        method: "POST",
        body: JSON.stringify({ message: question, conversation_id: conversation }),
      });
      setConversation(result.conversation_id);
      setMessages((current) => [
        ...current,
        { role: "user", content: question, source: "user" },
        {
          role: "assistant",
          content: result.message,
          tools: result.tools_used,
          source: "serversense",
        },
      ]);
      form.reset();
      void loadConversations();
    } catch (error) {
      setQuickTelemetryError(error instanceof Error ? error.message : "Direct telemetry failed");
    }
  };
  const openNotice = async (notice: Notice) => {
    await api(`/api/ai/notifications/${notice.id}/read`, { method: "POST" });
    if (notice.conversation_id) await openConversation(notice.conversation_id);
    await loadBackgroundState();
  };
  const dismissNotice = async (notice: Notice) => {
    await api<void>(`/api/ai/notifications/${notice.id}`, { method: "DELETE" });
    await loadBackgroundState();
  };
  const clearNotices = async () => {
    await api<void>("/api/ai/notifications", { method: "DELETE" });
    await loadBackgroundState();
  };
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const field = new FormData(event.currentTarget).get("message")?.toString() ?? "";
    event.currentTarget.reset();
    void ask(field);
  };
  const conversationJobs = jobs.filter(
    (job) => (!conversation || job.conversation_id === conversation) && !terminal.has(job.status),
  );
  // Jobs arrive newest first. A retry reuses the original user message, so only
  // its newest attempt should remain actionable.
  const newestAttemptIds = new Set<string>();
  const seenUserMessages = new Set<number>();
  for (const job of jobs) {
    if (job.user_message_id == null || !seenUserMessages.has(job.user_message_id)) {
      newestAttemptIds.add(job.id);
      if (job.user_message_id != null) seenUserMessages.add(job.user_message_id);
    }
  }
  const retryableJobs = jobs.filter(
    (job) =>
      (!conversation || job.conversation_id === conversation) &&
      newestAttemptIds.has(job.id) &&
      ["failed", "cancelled", "timed_out", "interrupted"].includes(job.status),
  );

  return (
    <div className="page sense-page">
      <PageHeader eyebrow="SERVER INTELLIGENCE" title="Ask SENSE">
        <button className="notice-button" onClick={() => setShowNotices((value) => !value)}>
          <Bell size={17} /> Notifications {unread > 0 && <b>{unread}</b>}
        </button>
      </PageHeader>
      {showNotices && (
        <section className="sense-notifications">
          <header><b>SENSE job notifications</b><span>{notices.length > 0 && <button onClick={() => void clearNotices()}>Clear all</button>}<button aria-label="Close notifications" onClick={() => setShowNotices(false)}><X size={15} /></button></span></header>
          {notices.map((notice) => (
            <div className={`sense-notification ${notice.read_at ? "" : "unread"}`} key={notice.id}>
              <button className="notice-content" onClick={() => void openNotice(notice)}>
                <b>{notice.title}</b><small>{notice.preview}</small>
              </button>
              <button className="notice-dismiss" aria-label={`Dismiss notification: ${notice.title}`} onClick={() => void dismissNotice(notice)}><X size={14} /></button>
            </div>
          ))}
          {!notices.length && <p>No SENSE job notifications yet.</p>}
        </section>
      )}
      <div className="sense-layout">
        <aside className="conversation-list">
          <div className="conversation-list-header"><div className="conversation-heading"><span className="eyebrow">CONVERSATIONS</span></div><button className="history-toggle" aria-expanded={showHistory} onClick={() => setShowHistory((value) => !value)}><span><span className="eyebrow">CONVERSATIONS</span><small>{conversation ? conversations.find((item) => item.id === conversation)?.title : "Start or open a chat"}</small></span><ChevronDown size={16} /></button><button onClick={() => { setConversation(undefined); setMessages([]); setShowHistory(false); }}>New</button></div>
          <div className={`conversation-list-body ${showHistory ? "open" : ""}`}>
            <label className="conversation-search"><Search size={14} /><input aria-label="Search conversations" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search" /></label>
            {conversations.map((item) => (
              <div className="conversation-item" key={item.id}>
                <button className={conversation === item.id ? "active" : ""} onClick={() => void openConversation(item.id)}>
                  <b>{item.title}</b><small>{formatDate(item.updated_at, timeZone)}</small>
                </button>
                <button className="conversation-edit" aria-label={`Rename conversation: ${item.title}`} onClick={() => void renameConversation(item)}><Pencil size={12} /></button>
                <button className="conversation-delete" aria-label={`Delete conversation: ${item.title}`} disabled={busy} onClick={() => void deleteConversation(item)}><Trash2 size={13} /></button>
              </div>
            ))}
            {!conversations.length && <p>Your SENSE conversations will appear here.</p>}
          </div>
        </aside>
        <div className="chat-panel">
          {messages.length === 0 && conversationJobs.length === 0 ? (
            <div className="chat-welcome"><span><Sparkles /></span><h2>What would you like to understand?</h2><p>ServerSense answers current factual telemetry directly. SENSE AI handles explanations and deeper analysis using bounded, read-only context.</p><div className="prompt-grid">{prompts.map((prompt) => <button key={prompt} onClick={() => void ask(prompt)}>{prompt}<Send size={14} /></button>)}</div></div>
          ) : (
            <div className="messages">
              {messages.map((message, index) => (
                <article key={message.id ?? index} className={message.role}>
                  <span>{message.role === "assistant" ? message.source === "serversense" ? <Server /> : <Bot /> : <User />}</span>
                  <div>{message.tools?.map((tool) => <small className="tool" key={tool}>Checked {tool.replace("get_", "").replaceAll("_", " ")}</small>)}<ReactMarkdown>{message.content}</ReactMarkdown>{message.role === "assistant" && <small className={`model ${message.source ?? "sense_ai"}`}>{message.source === "serversense" ? "ServerSense · live telemetry" : `SENSE AI · ${message.model ?? "configured model"}`}</small>}</div>
                </article>
              ))}
              {busy && (
                <article className="assistant">
                  <span><Bot /></span>
                  {draft ? (
                    <div>
                      <ReactMarkdown>{draft}</ReactMarkdown>
                      <small className="streaming-label">SENSE AI · Streaming</small>
                    </div>
                  ) : (
                    <div className="thinking"><i /><i /><i /> {activity}</div>
                  )}
                </article>
              )}
              {longRunningJob && (
                <section className="long-running-panel" aria-live="polite">
                  <div className="long-running-status">
                    <div>
                      <b>SENSE AI is still working.</b>
                      <p>You can stay here and continue watching, or leave this page and return later.</p>
                    </div>
                    <label>
                      <input
                        type="checkbox"
                        checked={foregroundNotify}
                        onChange={(event) => void setJobNotification(longRunningJob, event.target.checked)}
                      />
                      Notify me when complete
                    </label>
                  </div>
                  <form className="quick-telemetry" onSubmit={(event) => void askQuickTelemetry(event)}>
                    <input name="quick_message" placeholder="Ask a current telemetry question while SENSE AI works…" />
                    <button>Ask ServerSense</button>
                  </form>
                  {quickTelemetryError && <small className="quick-telemetry-error">{quickTelemetryError}</small>}
                </section>
              )}
              {conversationJobs.filter((job) => job.id !== activeStreamJob && job.id !== longRunningJob).map((job) => (
                <article className="job-card" key={job.id}>
                  <span><Clock3 /></span>
                  <div>
                    <b>SENSE AI · {job.status.replaceAll("_", " ")}</b>
                    <small>
                      {job.model}
                      {job.queue_position ? ` · queue #${job.queue_position}` : ""}
                      {job.queue_wait_seconds != null ? ` · waited ${Math.floor(job.queue_wait_seconds)}s` : ""}
                      {job.time_to_first_token_seconds != null ? ` · first token ${job.time_to_first_token_seconds.toFixed(1)}s` : ""}
                      {job.inference_seconds != null ? ` · runtime ${Math.floor(job.inference_seconds)}s` : ""}
                      {job.generated_tokens != null ? ` · ~${job.generated_tokens} tokens` : ""}
                    </small>
                    {job.partial_response && <ReactMarkdown>{job.partial_response}</ReactMarkdown>}
                    {job.backgrounded && (
                      <label className="job-notify-toggle">
                        <input
                          type="checkbox"
                          checked={job.notify_on_completion}
                          onChange={(event) => void setJobNotification(job.id, event.target.checked)}
                        />
                        Notify me when complete
                      </label>
                    )}
                    <div><button onClick={() => void cancelJob(job)}><Square size={12} /> Cancel analysis</button></div>
                  </div>
                </article>
              ))}
              {retryableJobs.slice(0, 3).map((job) => (
                <article className="job-card failed" key={job.id}>
                  <span><Bot /></span>
                  <div>
                    <b>SENSE job {job.status.replaceAll("_", " ")}</b>
                    <small>{job.error}</small>
                    <button disabled={retryingJobs.has(job.id)} onClick={() => void retryJob(job)}><RotateCcw size={12} /> {retryingJobs.has(job.id) ? "Retryingâ€¦" : "Retry"}</button>
                  </div>
                </article>
              ))}
            </div>
          )}
          <form className="chat-input" onSubmit={submit}><input name="message" placeholder="Ask about telemetry or request deeper analysis…" autoComplete="off" disabled={busy} />{busy ? <button type="button" className="stop" aria-label="Stop response" onClick={stop}><Square /></button> : <button aria-label="Send"><Send /></button>}</form>
        </div>
      </div>
    </div>
  );
}
