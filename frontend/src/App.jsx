import { useState, useRef, useEffect } from 'react';
import { GoogleLogin } from '@react-oauth/google';

// Helper to decode JWT payload safely
function decodeJwt(token) {
  try {
    const base64Url = token.split('.')[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const jsonPayload = decodeURIComponent(atob(base64).split('').map(function(c) {
      return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
    }).join(''));
    return JSON.parse(jsonPayload);
  } catch (e) {
    console.error('Failed to decode JWT', e);
    return null;
  }
}

export default function App() {
  const [authData, setAuthData] = useState(null); // { token, email, name }
  const [messages, setMessages] = useState([
    { role: 'ai', text: 'Hello! I am the Meridian Dynamics HR Assistant. How can I help you today?' }
  ]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef(null);

  // Auto-scroll to bottom of chat
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleLoginSuccess = (credentialResponse) => {
    const token = credentialResponse.credential;
    const payload = decodeJwt(token);
    
    if (payload && payload.email) {
      setAuthData({
        token,
        email: payload.email,
        name: payload.name || payload.email.split('@')[0]
      });
    } else {
      alert("Failed to extract email from Google login.");
    }
  };

  const handleSendMessage = async (e) => {
    e.preventDefault();
    if (!inputValue.trim() || !authData) return;

    const userText = inputValue.trim();
    setInputValue('');
    
    // Add user message to UI immediately
    setMessages(prev => [...prev, { role: 'user', text: userText }]);
    setIsLoading(true);

    try {
      const response = await fetch('http://localhost:8080/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${authData.token}`
        },
        body: JSON.stringify({ message: userText })
      });

      if (!response.ok) {
        throw new Error(`Server returned ${response.status}`);
      }

      const data = await response.json();
      
      // Add AI response to UI
      setMessages(prev => [...prev, { role: 'ai', text: data.response }]);
    } catch (error) {
      console.error('Chat error:', error);
      setMessages(prev => [...prev, { 
        role: 'ai', 
        text: 'Sorry, I encountered an error communicating with the server.' 
      }]);
    } finally {
      setIsLoading(false);
    }
  };

  if (!authData) {
    return (
      <div className="glass-container login-view">
        <h1 className="login-title">Meridian Dynamics</h1>
        <p className="login-subtitle">
          Internal HR Assistant Portal.<br/>
          Please authenticate with your employee account to continue.
        </p>
        <div style={{ marginTop: '20px' }}>
          <GoogleLogin
            onSuccess={handleLoginSuccess}
            onError={() => {
              console.error('Login Failed');
              alert('Google Login Failed');
            }}
            theme="filled_black"
            shape="pill"
          />
        </div>
      </div>
    );
  }

  return (
    <div className="glass-container chat-app">
      {/* Header */}
      <header className="chat-header">
        <h1>HR Assistant</h1>
        <div className="user-badge">
          <div className="indicator"></div>
          {authData.email}
        </div>
      </header>

      {/* Chat Messages */}
      <div className="messages-container">
        {messages.map((msg, idx) => (
          <div key={idx} className={`message-wrapper ${msg.role}`}>
            <div className="message-bubble whitespace-pre-wrap" style={{ whiteSpace: 'pre-wrap' }}>
              {msg.text}
            </div>
          </div>
        ))}
        {isLoading && (
          <div className="message-wrapper ai">
            <div className="message-bubble" style={{ padding: '16px' }}>
              <div className="typing-indicator">
                <span></span><span></span><span></span>
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Form */}
      <div className="input-area">
        <form onSubmit={handleSendMessage} className="input-form">
          <input
            type="text"
            className="chat-input"
            placeholder="Ask about PTO, benefits, or policies..."
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            disabled={isLoading}
          />
          <button 
            type="submit" 
            className="send-button"
            disabled={!inputValue.trim() || isLoading}
            title="Send Message"
          >
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
