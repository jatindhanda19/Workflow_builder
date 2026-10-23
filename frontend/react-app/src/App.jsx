import React from "react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  apiRequest,
  clearAuth,
  getSavedMessages,
  getSavedUser,
  saveAuth,
  saveMessages,
} from "./api.js";
import MermaidDiagram from "./MermaidDiagram.jsx";

const APP_NAME = "Flowwright";
const TAGLINE = [
  ["describe", "Describe it."],
  ["validate", "Validate it."],
  ["automate", "Automate it."],
];

// A gradient id unique to each SVG instance: Chrome does not paint a gradient whose first definition sits in a
// hidden element (the mobile brand is hidden on desktop), so instances must not share one.
function useSvgId(prefix) {
  return `${prefix}-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
}

// The Flowwright app icon: the "F" flow (start node, path, validated step, end node) on the purple tile.
function LogoMark({ className = "brand-mark" }) {
  const gradient = useSvgId("logo-bg");
  return (
    <svg className={className} viewBox="0 0 1024 1024" role="img" aria-label={APP_NAME}>
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#a48cff" />
          <stop offset="1" stopColor="#6f4be6" />
        </linearGradient>
      </defs>
      <rect width="1024" height="1024" rx="232" fill={`url(#${gradient})`} />
      <g fill="none" stroke="#fff" strokeLinecap="round" strokeWidth="76">
        <path d="M339 658V425a151 151 0 0 1 151-151h90" />
        <path d="M339 511h140" />
      </g>
      <circle cx="652" cy="274" r="76" fill="#fff" />
      <circle cx="339" cy="749" r="54" fill="none" stroke="#fff" strokeWidth="38" />
      <circle cx="555" cy="511" r="65" fill="#4ade80" />
      <path d="M523 515l25 24 40-48" fill="none" stroke="#0b0d12" strokeWidth="20" strokeLinecap="round"
        strokeLinejoin="round" />
    </svg>
  );
}

// The "F" flow on its own, for small spots that already have a tile (assistant avatar, welcome symbol).
function LogoGlyph({ size = 18 }) {
  const gradient = useSvgId("logo-node");
  return (
    <svg width={size} height={size} viewBox="177 190 640 640" aria-hidden="true">
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#a48cff" />
          <stop offset="1" stopColor="#7aa0ff" />
        </linearGradient>
      </defs>
      <g fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="76">
        <path d="M339 658V425a151 151 0 0 1 151-151h90" />
        <path d="M339 511h140" />
      </g>
      <circle cx="652" cy="274" r="76" fill={`url(#${gradient})`} />
      <circle cx="339" cy="749" r="54" fill="none" stroke="currentColor" strokeWidth="38" />
      <circle cx="555" cy="511" r="65" fill="#4ade80" />
      <path d="M523 515l25 24 40-48" fill="none" stroke="#0b0d12" strokeWidth="20" strokeLinecap="round"
        strokeLinejoin="round" />
    </svg>
  );
}

function Brand({ className = "" }) {
  return (
    <div className={`brand ${className}`.trim()}>
      <LogoMark />
      <span className="brand-name">{APP_NAME}</span>
    </div>
  );
}

function Tagline() {
  return (
    <div className="tagline">
      {TAGLINE.map(([key, text]) => (
        <span key={key}><i className={`tagline-dot ${key}`} aria-hidden="true" />{text}</span>
      ))}
    </div>
  );
}

function Icon({ name, size = 18 }) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
  };

  const paths = {
    send: <><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></>,
    plus: <><path d="M12 5v14M5 12h14" /></>,
    logout: <><path d="M10 17l5-5-5-5" /><path d="M15 12H3" /><path d="M12 3h7a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-7" /></>,
    workflow: <><rect x="3" y="3" width="7" height="6" rx="1.5" /><rect x="14" y="15" width="7" height="6" rx="1.5" /><path d="M6.5 9v4a2 2 0 0 0 2 2H17v0" /><path d="M14 18h-4" /></>,
    shield: <><path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z" /><path d="m9 12 2 2 4-4" /></>,
    activity: <><path d="M3 12h4l3-9 4 18 3-9h4" /></>,
    chevron: <path d="m9 18 6-6-6-6" />,
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6M8 13h8M8 17h8" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    play: <path d="m7 4 14 8-14 8Z" />,
    copy: <><rect x="8" y="8" width="13" height="13" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></>,
  };

  return <svg {...common}>{paths[name] || paths.workflow}</svg>;
}

function AuthScreen({ onAuthenticated }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function submit(event) {
    event.preventDefault();
    setError("");
    setNotice("");

    const normalizedEmail = email.trim();

    if (!normalizedEmail || !password) {
      setError("Enter your email and password.");
      return;
    }

    if (mode === "register") {
      if (password.length < 12 || password.length > 128) {
        setError("Password must be between 12 and 128 characters.");
        return;
      }

      if (password !== confirmPassword) {
        setError("The passwords do not match.");
        return;
      }
    }

    setBusy(true);

    try {
      if (mode === "register") {
        await apiRequest("/auth/register", {
          method: "POST",
          body: JSON.stringify({
            email: normalizedEmail,
            password,
          }),
        });

        setNotice("Account created. You can now sign in.");
        setMode("login");
        setPassword("");
        setConfirmPassword("");
      } else {
        const data = await apiRequest("/auth/login", {
          method: "POST",
          body: JSON.stringify({
            email: normalizedEmail,
            password,
          }),
        });

        saveAuth(data.access_token, data.user);
        onAuthenticated(data.user);
      }
    } catch (err) {
      setError(err.message || "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-layout">
      <section className="auth-story">
        <Brand />

        <div className="story-content">
          <div className="eyebrow"><span className="status-dot" /> INTELLIGENT AUTOMATION</div>
          <h1>Turn ideas into<br /><span>working flows.</span></h1>
          <p>
            Describe what you want to accomplish. Build structured AI-powered
            workflows, inspect each step, and execute them from one workspace.
          </p>

          <div className="story-points">
            <div><span className="point-icon"><Icon name="workflow" /></span><span><b>Natural-language planning</b><small>Describe a task in your own words</small></span></div>
            <div><span className="point-icon"><Icon name="shield" /></span><span><b>Security-aware by design</b><small>Authenticated, owner-scoped sessions</small></span></div>
            <div><span className="point-icon"><Icon name="activity" /></span><span><b>Inspectable workflows</b><small>Review steps before execution</small></span></div>
          </div>
        </div>

        <Tagline />
      </section>

      <section className="auth-panel">
        <div className="auth-card">
          <Brand className="mobile-brand" />

          <div className="auth-heading">
            <div className="mini-label">{mode === "login" ? "WELCOME BACK" : "GET STARTED"}</div>
            <h2>{mode === "login" ? "Sign in to your workspace" : "Create your workspace account"}</h2>
            <p>{mode === "login" ? "Enter your details to continue building." : "Create an account to start building workflows."}</p>
          </div>

          <form onSubmit={submit} className="auth-form">
            <label htmlFor="email">Email address</label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />

            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              placeholder={mode === "register" ? "At least 12 characters" : "Enter your password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              minLength={mode === "register" ? 12 : undefined}
              maxLength={128}
              required
            />

            {mode === "register" && (
              <>
                <label htmlFor="confirm-password">Confirm password</label>
                <input
                  id="confirm-password"
                  type="password"
                  autoComplete="new-password"
                  placeholder="Enter your password again"
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  maxLength={128}
                  required
                />
                <p className="form-hint">Use 12–128 characters. Choose a unique password.</p>
              </>
            )}

            {error && <div className="notice error-notice" role="alert">{error}</div>}
            {notice && <div className="notice success-notice" role="status">{notice}</div>}

            <button className="primary-button auth-submit" type="submit" disabled={busy}>
              {busy ? <span className="spinner" /> : null}
              {busy ? "Please wait..." : mode === "login" ? "Sign in" : "Create account"}
              {!busy && <Icon name="chevron" size={17} />}
            </button>
          </form>

          <div className="auth-switch">
            {mode === "login" ? `New to ${APP_NAME}?` : "Already have an account?"}
            {" "}
            <button
              type="button"
              onClick={() => {
                setMode(mode === "login" ? "register" : "login");
                setError("");
                setNotice("");
              }}
            >
              {mode === "login" ? "Create account" : "Sign in"}
            </button>
          </div>

          <div className="auth-security"><Icon name="shield" size={15} /> Your workspace is protected by authentication.</div>
        </div>
      </section>
    </main>
  );
}

function formatWhen(timestamp) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "";
  const sameDay = date.toDateString() === new Date().toDateString();
  return sameDay
    ? date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : date.toLocaleDateString([], { day: "numeric", month: "short" });
}

// Server messages ({role, content, options, parts}) as the chat list shows them.
function toChatMessages(messages = []) {
  return messages.map((message) => ({ role: message.role, content: message.content }));
}

function Sidebar({ user, sessionId, sessions, busy, onNew, onSelect, onLogout }) {
  return (
    <aside className="sidebar">
      <Brand className="sidebar-brand" />

      <div className="sidebar-label">WORKSPACE</div>
      <button className="new-workflow-button" onClick={onNew} disabled={busy}>
        <Icon name="plus" size={17} /> New workflow
      </button>

      {sessions.length > 0 ? (
        <nav className="session-list" aria-label="Your sessions">
          <div className="sidebar-label">RECENT</div>
          {sessions.map((session) => (
            <button
              key={session.id}
              type="button"
              className={`session-item ${session.id === sessionId ? "active" : ""}`}
              onClick={() => onSelect(session.id)}
              disabled={busy}
              aria-current={session.id === sessionId ? "page" : undefined}
              title={session.title}
            >
              <span className="session-title">{session.title}</span>
              <small>{session.generated ? "Workflow ready" : "In progress"} · {formatWhen(session.updated_at)}</small>
            </button>
          ))}
        </nav>
      ) : (
        <div className="sidebar-card">
          <div className="sidebar-card-icon"><Icon name="activity" /></div>
          <div><b>AI Workflow Engine</b><p>Describe a task to get started.</p></div>
        </div>
      )}

      <div className="sidebar-bottom">
        <div className="connection-status"><span className="status-dot" /><span>Authenticated session</span></div>
        {sessionId && <div className="session-reference">Session · {sessionId.slice(0, 8)}…</div>}
        <div className="user-card">
          <div className="avatar">{(user?.email || "U").charAt(0).toUpperCase()}</div>
          <div className="user-info"><b>{user?.email || "User"}</b><small>Workspace member</small></div>
          <button className="icon-button logout-button" onClick={onLogout} title="Sign out" aria-label="Sign out"><Icon name="logout" /></button>
        </div>
      </div>
    </aside>
  );
}

function FieldPanel({ fields = [], steps = [], workflow, mode }) {
  const [tab, setTab] = useState("fields");

  const tabs = [
    { id: "fields", label: "Fields", count: fields.length },
    { id: "steps", label: "Steps", count: steps.length },
    { id: "json", label: "Workflow", count: null },
  ];

  return (
    <section className="details-panel">
      <div className="panel-heading">
        <div><div className="mini-label">WORKFLOW DETAILS</div><h3>Workspace inspector</h3></div>
        <span className={`mode-badge ${mode === "ready" ? "ready" : ""}`}>{mode || "collecting"}</span>
      </div>

      <div className="inspector-tabs">
        {tabs.map((item) => (
          <button key={item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}>
            {item.label}{item.count !== null && <span>{item.count}</span>}
          </button>
        ))}
      </div>

      <div className="inspector-content">
        {tab === "fields" && (
          fields.length ? fields.map((field, index) => (
            <div className="field-row" key={field.key || index}>
              <div className="field-row-top">
                <span className="field-name">{field.key || field.parameter || "Field"}</span>
                <span className={`field-status ${field.status || "missing"}`}>{field.status || "pending"}</span>
              </div>
              <div className="field-value">{field.display || (field.value == null ? "Awaiting input" : String(field.value))}</div>
              {field.note && <div className="field-note">{field.note}</div>}
            </div>
          )) : <div className="empty-inspector"><Icon name="file" size={23} /><b>No fields collected yet</b><p>Start a conversation to populate workflow fields.</p></div>
        )}

        {tab === "steps" && (
          steps.length ? steps.map((step, index) => (
            <div className="step-row" key={step.id || index}>
              <div className="step-number">{String(index + 1).padStart(2, "0")}</div>
              <div>
                <b>{step.title || step.operation || `Step ${index + 1}`}</b>
                <p>{step.app || step.kind || "Workflow operation"}</p>
                <div className="step-badges">
                  {step.app && (step.verified
                    ? <span className="step-badge verified" title="Checked against the app catalogue">Verified</span>
                    : <span className="step-badge" title="Not in the app catalogue: its settings are not checked">Unverified</span>)}
                  {step.executable
                    ? <span className="step-badge runs" title="The built-in runner can execute this step">Runs here</span>
                    : <span className="step-badge" title="Planned, not executable here">Planned only</span>}
                </div>
              </div>
            </div>
          )) : <div className="empty-inspector"><Icon name="workflow" size={23} /><b>No steps yet</b><p>Your generated workflow will appear here.</p></div>
        )}

        {tab === "json" && (
          workflow ? <pre className="workflow-json">{JSON.stringify(workflow, null, 2)}</pre>
            : <div className="empty-inspector"><Icon name="file" size={23} /><b>No workflow generated</b><p>Complete the conversation to generate a workflow.</p></div>
        )}
      </div>

      <div className="inspector-footer"><Icon name="shield" size={15} /> Workflow data is scoped to your account.</div>
    </section>
  );
}

function ChatMessage({ message }) {
  return (
    <div className={`message-row ${message.role === "user" ? "user-message-row" : ""}`}>
      {message.role !== "user" && <div className="assistant-avatar"><LogoGlyph size={17} /></div>}
      <div className="message-content">
        <div className="message-author">{message.role === "user" ? "You" : "Workflow Assistant"}</div>
        <div className={`message-bubble ${message.role === "user" ? "user-bubble" : ""}`}>
          {message.content}
        </div>
      </div>
      {message.role === "user" && <div className="user-avatar">{message.content?.charAt(0)?.toUpperCase() || "Y"}</div>}
    </div>
  );
}

function App() {
  const [user, setUser] = useState(() => getSavedUser());
  const [token, setToken] = useState(() => sessionStorage.getItem("wb_access_token"));
  const [sessionId, setSessionId] = useState(() => sessionStorage.getItem("wb_session_id") || "");
  const [messages, setMessages] = useState(() => getSavedMessages());
  const [sessions, setSessions] = useState([]);
  const [view, setView] = useState(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [runInput, setRunInput] = useState("{}");
  const [runResult, setRunResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [showRun, setShowRun] = useState(false);
  const [copied, setCopied] = useState(false);

  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  const authenticated = Boolean(token && user);

  const signOut = useCallback(() => {
    clearAuth();
    setToken(null);
    setUser(null);
    setSessionId("");
    setMessages([]);
    setSessions([]);
    setView(null);
    setRunResult(null);
    setError("");
  }, []);

  // The sidebar list. A failure only leaves the list as it was: the chat itself still works.
  const refreshSessions = useCallback(async () => {
    try {
      const list = await apiRequest("/sessions");
      setSessions(Array.isArray(list) ? list : []);
    } catch {
      // apiRequest already signs the user out on 401; other errors are shown when they matter (sending a message).
    }
  }, []);

  // Shows a stored session: its messages and workflow state come from the server, the source of truth.
  const showSession = useCallback((session) => {
    setView(session);
    setMessages(toChatMessages(session.messages));
    setSessionId(session.session_id);
    sessionStorage.setItem("wb_session_id", session.session_id);
    setRunResult(null);
    setShowRun(false);
  }, []);

  useEffect(() => {
    const handleExpired = () => {
      signOut();
      setError("Your session expired. Please sign in again.");
    };

    window.addEventListener("wb-auth-expired", handleExpired);
    return () => window.removeEventListener("wb-auth-expired", handleExpired);
  }, [signOut]);

  // Runs once on sign-in or page load: checks the token, loads the session list and reopens the last session.
  // It must not re-run when sessionId changes: a new session only exists after its first /chat reply.
  useEffect(() => {
    if (!authenticated) return;

    let cancelled = false;
    const savedId = sessionStorage.getItem("wb_session_id");

    async function restoreSession() {
      try {
        await apiRequest("/auth/me");
        if (cancelled) return;
        refreshSessions();
        if (!savedId) return;

        try {
          const restored = await apiRequest(`/sessions/${encodeURIComponent(savedId)}`);
          if (!cancelled && sessionStorage.getItem("wb_session_id") === savedId) showSession(restored);
        } catch (err) {
          if (err.status !== 404) throw err;
          // Gone on the backend: forget it, unless the user has started another session meanwhile.
          if (sessionStorage.getItem("wb_session_id") === savedId) {
            sessionStorage.removeItem("wb_session_id");
            if (!cancelled) setSessionId("");
          }
        }
      } catch (err) {
        if (!cancelled) setError(err.message || "Could not restore your session.");
      }
    }

    restoreSession();

    return () => {
      cancelled = true;
    };
  }, [authenticated, refreshSessions, showSession]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  useEffect(() => {
    saveMessages(messages);
  }, [messages]);

  function handleAuthenticated(nextUser) {
    setUser(nextUser);
    setToken(sessionStorage.getItem("wb_access_token"));
    setError("");
  }

  function newWorkflow() {
    setMessages([]);
    setView(null);
    setRunResult(null);
    setError("");
    setShowRun(false);
    setRunInput("{}");

    // No id yet: the server creates the session, and its id, on the first message.
    sessionStorage.removeItem("wb_session_id");
    sessionStorage.setItem("wb_messages", "[]");
    setSessionId("");

    inputRef.current?.focus();
  }

  async function openSession(id) {
    if (busy || id === sessionId) return;
    setError("");
    try {
      showSession(await apiRequest(`/sessions/${encodeURIComponent(id)}`));
    } catch (err) {
      setError(err.message || "Could not open that session.");
      if (err.status === 404) refreshSessions();  // deleted meanwhile: drop it from the list
    }
  }

  async function sendMessage(text) {
    const message = text.trim();

    if (!message || busy) return;

    if (message.length > 4000) {
      setError("Please keep your message under 4,000 characters.");
      return;
    }

    setError("");
    setBusy(true);
    setInput("");

    const userMessage = { role: "user", content: message };
    setMessages((current) => [...current, userMessage]);

    try {
      const response = await apiRequest("/chat", {
        method: "POST",
        body: JSON.stringify({
          session_id: sessionId || undefined,  // omitted for a new chat: the server creates the session
          message,
        }),
      });

      setView(response);
      setRunResult(null);
      sessionStorage.setItem("wb_session_id", response.session_id);
      setSessionId(response.session_id);
      refreshSessions();

      const assistantMessage = {
        role: "assistant",
        content: response.reply || "Your workflow has been updated.",
      };

      setMessages((current) => [...current, assistantMessage]);
    } catch (err) {
      setError(err.message || "Could not send your message.");
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content: "I couldn't process that message. Check the error notice and try again.",
          failed: true,
        },
      ]);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  async function executeWorkflow() {
    if (!sessionId || !view?.workflow) {
      setError("Generate a workflow before trying to execute it.");
      return;
    }

    let parsedInput;

    try {
      parsedInput = JSON.parse(runInput || "{}");
      if (!parsedInput || Array.isArray(parsedInput) || typeof parsedInput !== "object") {
        throw new Error("Input must be a JSON object.");
      }
    } catch (err) {
      setError(`Invalid JSON input: ${err.message}`);
      return;
    }

    setRunning(true);
    setError("");
    setRunResult(null);

    try {
      const result = await apiRequest(
        `/sessions/${encodeURIComponent(sessionId)}/run`,
        {
          method: "POST",
          body: JSON.stringify({ input: parsedInput }),
        }
      );

      setRunResult(result);
    } catch (err) {
      setError(err.message || "Workflow execution failed.");
    } finally {
      setRunning(false);
    }
  }

  async function startOver() {
    if (busy) return;
    newWorkflow();
  }

  async function copyDiagram() {
    if (!view?.diagram) return;

    try {
      await navigator.clipboard.writeText(view.diagram);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Clipboard access failed. You can select and copy the diagram manually.");
    }
  }

  if (!authenticated) {
    return <AuthScreen onAuthenticated={handleAuthenticated} />;
  }

  const ready = Boolean(view?.workflow && view?.all_collected);
  const hasMessages = messages.length > 0;
  // The backend leaves a question's options out of the reply text; they are shown as buttons here.
  const question = view?.question;
  // Steps the built-in runner cannot execute: the workflow is then planned only, never half run.
  const notRunnable = (view?.steps || []).filter((step) => step.executable === false);

  return (
    <div className="app-shell">
      <Sidebar
        user={user}
        sessionId={sessionId}
        sessions={sessions}
        busy={busy}
        onNew={startOver}
        onSelect={openSession}
        onLogout={signOut}
      />

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumbs">
            <span>Workspace</span><Icon name="chevron" size={14} /><b>Workflow builder</b>
          </div>
          <div className="topbar-right">
            <span className="secure-pill"><Icon name="shield" size={14} /> Secure workspace</span>
            <div className="topbar-avatar">{(user?.email || "U").charAt(0).toUpperCase()}</div>
          </div>
        </header>

        <div className="workspace-grid">
          <section className="chat-panel">
            <div className="chat-heading">
              <div>
                <div className="mini-label">YOUR AI WORKSPACE</div>
                <h1>Workflow builder</h1>
                <p>Describe your goal. We'll turn it into an actionable workflow.</p>
              </div>
              <div className={`status-pill ${ready ? "status-ready" : ""}`}>
                <span className="status-dot" /> {ready ? "Workflow ready" : "Gathering requirements"}
              </div>
            </div>

            <div className="chat-body">
              {!hasMessages && (
                <div className="welcome-block">
                  <div className="welcome-symbol"><LogoGlyph size={31} /></div>
                  <div className="mini-label">LET'S BUILD SOMETHING</div>
                  <h2>What would you like<br />to <span>automate today?</span></h2>
                  <p>Start with a goal. You can refine the details as we go.</p>
                  <div className="suggestion-grid">
                    {[
                      ["Research assistant", "Build a workflow to summarize research papers."],
                      ["Data processing", "Create a workflow to validate and transform a CSV file."],
                      ["Content pipeline", "Build a workflow to draft and review a blog post."],
                    ].map(([title, prompt]) => (
                      <button key={title} className="suggestion-card" onClick={() => sendMessage(prompt)} disabled={busy}>
                        <span>{title}</span><p>{prompt}</p><Icon name="chevron" size={16} />
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {hasMessages && (
                <div className="message-list">
                  {messages.map((message, index) => (
                    <ChatMessage key={`${index}-${message.role}`} message={message} />
                  ))}
                  {busy && (
                    <div className="message-row">
                      <div className="assistant-avatar"><LogoGlyph size={17} /></div>
                      <div className="message-content"><div className="message-author">Workflow Assistant</div><div className="typing-indicator"><i /><i /><i /></div></div>
                    </div>
                  )}
                  <div ref={bottomRef} />
                </div>
              )}
            </div>

            {error && (
              <div className="error-banner" role="alert">
                <span>{error}</span>
                <button onClick={() => setError("")} aria-label="Dismiss error">×</button>
              </div>
            )}

            {question?.options?.length > 0 && !busy && (
              <div className="answer-options" role="group" aria-label="Suggested answers">
                {question.options.map((option, index) => (
                  <button key={option} type="button" className="answer-option" onClick={() => sendMessage(option)}>
                    <span>{String.fromCharCode(65 + index)}</span>{option}
                  </button>
                ))}
              </div>
            )}

            <form
              className="composer"
              onSubmit={(event) => {
                event.preventDefault();
                sendMessage(input);
              }}
            >
              <textarea
                ref={inputRef}
                rows="1"
                maxLength={4000}
                placeholder={
                  question && !question.allow_custom
                    ? "Pick one of the options above..."
                    : "Describe the workflow you want to build..."
                }
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    sendMessage(input);
                  }
                }}
                disabled={busy}
              />
              <div className="composer-bottom">
                <span>Be specific for better results <span className="keyboard-hint">· Enter to send</span></span>
                <div className="composer-actions">
                  <span className="character-count">{input.length}/4000</span>
                  <button type="submit" className="send-button" disabled={busy || !input.trim()} aria-label="Send message">
                    <Icon name="send" size={17} /> <span>Send</span>
                  </button>
                </div>
              </div>
            </form>

            <div className="chat-disclaimer">AI-generated workflows may need review. Inspect the workflow before execution.</div>
          </section>

          <aside className="right-column">
            <FieldPanel
              fields={view?.fields || []}
              steps={view?.steps || []}
              workflow={view?.workflow}
              mode={view?.mode}
            />

            <section className="diagram-panel">
              <div className="panel-heading">
                <div><div className="mini-label">VISUAL OVERVIEW</div><h3>Workflow diagram</h3></div>
                {view?.diagram && <button className="icon-button" onClick={copyDiagram} title="Copy diagram source" aria-label="Copy diagram">{copied ? <Icon name="check" /> : <Icon name="copy" />}</button>}
              </div>

              {view?.diagram ? (
                <MermaidDiagram source={view.diagram} />
              ) : (
                <div className="diagram-empty">
                  <div className="diagram-placeholder">
                    <div className="diagram-node">Your goal</div>
                    <div className="diagram-line" />
                    <div className="diagram-node muted-node">Workflow steps</div>
                    <div className="diagram-line" />
                    <div className="diagram-node muted-node">Result</div>
                  </div>
                  <p>Your diagram will appear when a workflow is generated.</p>
                </div>
              )}
            </section>

            {view?.workflow && (
              <section className="execute-panel">
                <div className="panel-heading">
                  <div><div className="mini-label">EXECUTION</div><h3>Run workflow</h3></div>
                  <span className="ready-dot" />
                </div>
                <p className="execute-description">Review the generated workflow and provide any required input before running it.</p>
                {notRunnable.length > 0 && (
                  <div className="run-warning" role="status">
                    <b>Planned, not executable here:</b> {notRunnable.map((s) => s.title).join(", ")}.
                    {" "}This runner executes Manual and Webhook triggers, If, HTTP Request and Slack messages, and
                    runs a workflow only when every step is one of them.
                  </div>
                )}
                <button className="secondary-button full-button" onClick={() => setShowRun(!showRun)}>
                  <Icon name="play" size={16} /> {showRun ? "Hide execution options" : "Configure execution"}
                </button>

                {showRun && (
                  <div className="run-config">
                    <label htmlFor="run-input">Input JSON</label>
                    <textarea id="run-input" value={runInput} onChange={(event) => setRunInput(event.target.value)} rows={5} spellCheck="false" />
                    <button className="primary-button full-button" onClick={executeWorkflow}
                      disabled={running || notRunnable.length > 0}>
                      {running ? <span className="spinner" /> : <Icon name="play" size={15} />}
                      {running ? "Running..." : "Execute workflow"}
                    </button>
                  </div>
                )}

                {runResult && (
                  <div className="run-results">
                    <b>Execution results</b>
                    {runResult.steps?.length ? runResult.steps.map((step, index) => (
                      <div className="result-step" key={step.id || index}>
                        <div><span className={`result-status ${step.status === "ok" ? "result-ok" : ""}`} />{step.name || `Step ${index + 1}`}</div>
                        <span className="result-status-text">{step.status}</span>
                        {step.error && <p>{step.error}</p>}
                        {step.output !== undefined && step.output !== null && <pre>{JSON.stringify(step.output, null, 2)}</pre>}
                      </div>
                    )) : <pre>{JSON.stringify(runResult, null, 2)}</pre>}
                  </div>
                )}
              </section>
            )}
          </aside>
        </div>

        <footer className="app-footer">
          <span>{APP_NAME} <span className="footer-separator">/</span> Describe it. Validate it. Automate it.</span>
          <span><span className="status-dot" /> API-connected workspace</span>
        </footer>
      </main>
    </div>
  );
}

export default App;
