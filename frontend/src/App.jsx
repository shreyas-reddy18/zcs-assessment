import { useState, useRef, useEffect } from 'react';
import { GoogleLogin, googleLogout } from '@react-oauth/google';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const GREETING = {
  role: 'ai',
  text: 'Hello! I can answer questions about Meridian Dynamics policies and your PTO, and submit PTO requests for you.',
};

function emailFromIdToken(token) {
  try {
    const payload = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(payload)).email;
  } catch {
    return null;
  }
}

async function api(path, token, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body ?? {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || `Server returned ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

export default function App() {
  const [auth, setAuth] = useState(null); // { token, email }
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([GREETING]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const signOut = (notice) => {
    googleLogout();
    setAuth(null);
    setSessionId(null);
    setMessages([GREETING]);
    if (notice) alert(notice);
  };

  const startConversation = async (token) => {
    const { session_id } = await api('/api/sessions', token);
    setSessionId(session_id);
    setMessages([GREETING]);
  };

  const handleLogin = async ({ credential }) => {
    const email = emailFromIdToken(credential);
    setAuth({ token: credential, email });
    try {
      await startConversation(credential);
    } catch (e) {
      signOut(`Sign-in failed: ${e.message}`);
    }
  };

  const send = async (e) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || !auth || !sessionId || busy) return;
    setInput('');
    setMessages((m) => [...m, { role: 'user', text }]);
    setBusy(true);
    try {
      const data = await api('/api/chat', auth.token, { session_id: sessionId, message: text });
      setMessages((m) => [...m, { role: 'ai', text: data.reply, tools: data.tools_used }]);
    } catch (err) {
      if (err.status === 401) {
        signOut('Your Google sign-in expired. Please sign in again.');
        return;
      }
      setMessages((m) => [...m, { role: 'ai', text: `Sorry, something went wrong: ${err.message}` }]);
    } finally {
      setBusy(false);
    }
  };

  if (!auth) {
    return (
      <div className="glass-container login-view">
        <h1 className="login-title">Meridian Dynamics</h1>
        <p className="login-subtitle">
          Internal HR Assistant.<br />
          Sign in with Google to continue.
        </p>
        <div style={{ marginTop: '20px' }}>
          <GoogleLogin onSuccess={handleLogin} onError={() => alert('Google sign-in failed')} theme="filled_black" shape="pill" />
        </div>
      </div>
    );
  }

  return (
    <div className="glass-container chat-app">
      <header className="chat-header">
        <h1>HR Assistant</h1>
        <div className="header-actions">
          <div className="user-badge">
            <div className="indicator"></div>
            {auth.email}
          </div>
          <button className="link-button" onClick={() => startConversation(auth.token)} disabled={busy}>
            New chat
          </button>
          <button className="link-button" onClick={() => signOut()}>Sign out</button>
        </div>
      </header>

      <div className="messages-container">
        {messages.map((msg, i) => (
          <div key={i} className={`message-wrapper ${msg.role}`}>
            <div className="message-bubble">
              {msg.role === 'ai' ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown> : msg.text}
              {msg.tools?.length > 0 && <div className="tools-used">Used: {[...new Set(msg.tools)].join(', ')}</div>}
            </div>
          </div>
        ))}
        {busy && (
          <div className="message-wrapper ai">
            <div className="message-bubble" style={{ padding: '16px' }}>
              <div className="typing-indicator"><span></span><span></span><span></span></div>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="input-area">
        <form onSubmit={send} className="input-form">
          <input
            type="text"
            className="chat-input"
            placeholder={sessionId ? 'Ask about PTO, benefits, or policies...' : 'Starting conversation...'}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy || !sessionId}
          />
          <button type="submit" className="send-button" disabled={!input.trim() || busy || !sessionId} title="Send">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="22" y1="2" x2="11" y2="13"></line>
              <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
            </svg>
          </button>
        </form>
      </div>
    </div>
  );
}
