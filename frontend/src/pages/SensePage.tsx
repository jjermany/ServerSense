import { FormEvent, useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Bot, Send, Sparkles, User } from "lucide-react";
import { PageHeader } from "../components/UI";
import { api } from "../api";
type Message = {
  role: "user" | "assistant";
  content: string;
  tools?: string[];
  model?: string;
};
type Conversation = { id: number; title: string; updated_at: string };
const prompts = [
  "How long until I run out of storage?",
  "Is any drive showing signs of failure?",
  "Are all my Docker containers healthy?",
  "What changed on my server today?",
];
export default function SensePage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversation, setConversation] = useState<number>();
  const [busy, setBusy] = useState(false);
  const [activity, setActivity] = useState("Checking server telemetry…");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const loadConversations = useCallback(
    () => api<Conversation[]>("/api/ai/conversations").then(setConversations),
    [],
  );
  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);
  const openConversation = async (id: number) => {
    const result = await api<{
      id: number;
      messages: { role: "user" | "assistant"; content: string }[];
    }>(`/api/ai/conversations/${id}`);
    setConversation(result.id);
    setMessages(result.messages);
  };
  const ask = async (text: string) => {
    if (!text.trim() || busy) return;
    setMessages((x) => [...x, { role: "user", content: text }]);
    setBusy(true);
    try {
      const response = await fetch("/api/ai/chat/stream", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, conversation_id: conversation }),
      });
      if (!response.ok || !response.body) {
        throw new Error(`SENSE request failed (${response.status})`);
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
          };
          if (event === "activity") setActivity(data.message);
          if (event === "error") throw new Error(data.message);
          if (event === "message") {
            setConversation(data.conversation_id);
            setMessages((x) => [
              ...x,
              {
                role: "assistant",
                content: data.message,
                tools: data.tools_used,
                model: data.model,
              },
            ]);
          }
        }
        if (done) break;
      }
    } catch (e) {
      setMessages((x) => [
        ...x,
        {
          role: "assistant",
          content: `I couldn't complete that request: ${e instanceof Error ? e.message : "Unknown error"}`,
        },
      ]);
    } finally {
      setBusy(false);
      void loadConversations();
    }
  };
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const field =
      new FormData(e.currentTarget).get("message")?.toString() ?? "";
    e.currentTarget.reset();
    ask(field);
  };
  return (
    <div className="page sense-page">
      <PageHeader eyebrow="SERVER INTELLIGENCE" title="Ask SENSE">
        <span className="powered">
          <i /> Powered by your telemetry
        </span>
      </PageHeader>
      <div className="sense-layout">
        <aside className="conversation-list">
          <div>
            <span className="eyebrow">CONVERSATIONS</span>
            <button
              onClick={() => {
                setConversation(undefined);
                setMessages([]);
              }}
            >
              New
            </button>
          </div>
          {conversations.map((item) => (
            <button
              key={item.id}
              className={conversation === item.id ? "active" : ""}
              onClick={() => openConversation(item.id)}
            >
              <b>{item.title}</b>
              <small>{new Date(item.updated_at).toLocaleDateString()}</small>
            </button>
          ))}
          {!conversations.length && (
            <p>Your SENSE conversations will appear here.</p>
          )}
        </aside>
        <div className="chat-panel">
          {messages.length === 0 ? (
            <div className="chat-welcome">
              <span>
                <Sparkles />
              </span>
              <h2>What would you like to understand?</h2>
              <p>
                SENSE uses read-only ServerSense tools to answer from measured
                data. It cannot run commands or modify your server.
              </p>
              <div className="prompt-grid">
                {prompts.map((p) => (
                  <button key={p} onClick={() => ask(p)}>
                    {p}
                    <Send size={14} />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="messages">
              {messages.map((m, i) => (
                <article key={i} className={m.role}>
                  <span>{m.role === "assistant" ? <Bot /> : <User />}</span>
                  <div>
                    {m.tools?.map((t) => (
                      <small className="tool" key={t}>
                        Checked {t.replace("get_", "").replaceAll("_", " ")}
                      </small>
                    ))}
                    <ReactMarkdown>{m.content}</ReactMarkdown>
                    {m.model && (
                      <small className="model">SENSE · {m.model}</small>
                    )}
                  </div>
                </article>
              ))}
              {busy && (
                <article className="assistant">
                  <span>
                    <Bot />
                  </span>
                  <div className="thinking">
                    <i />
                    <i />
                    <i /> {activity}
                  </div>
                </article>
              )}
            </div>
          )}
          <form className="chat-input" onSubmit={submit}>
            <input
              name="message"
              placeholder="Ask about storage, health, disks, containers…"
              autoComplete="off"
            />
            <button disabled={busy} aria-label="Send">
              <Send />
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
