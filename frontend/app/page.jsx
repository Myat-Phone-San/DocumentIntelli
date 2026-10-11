'use client';

import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ArrowUp, BookOpen, Check, ChevronDown, Copy, FileText, LockKeyhole, LogOut, Menu, MessageSquare, Plus, Search, ShieldCheck, Sparkles, Trash2, Upload, X } from 'lucide-react';

const suggestions = [
  ['Summarize a topic', 'Summarize the main topics covered in the approved documents.'],
  ['Compare the details', 'Compare the available account features in the documents.'],
  ['Explain it simply', 'Explain the key terms and conditions in simple language.'],
  ['Find an answer', 'What information is available about digital banking services?'],
];
const freshChat = () => ({ id: crypto.randomUUID(), title: 'New conversation', messages: [] });
let browserIdentity;
function ownerIdentity() {
  if (browserIdentity) return browserIdentity;
  try {
    const stored = localStorage.getItem('intelligence-browser-id');
    if (stored && /^[a-f0-9-]{36}$/i.test(stored)) browserIdentity = stored;
  } catch { /* Fall back to an in-memory identity. */ }
  if (!browserIdentity) {
    browserIdentity = crypto.randomUUID();
    try { localStorage.setItem('intelligence-browser-id', browserIdentity); } catch { /* In-memory only. */ }
  }
  return browserIdentity;
}

async function request(path, options = {}) {
  const response = await fetch(`/api/${path}`, { ...options, headers: { 'X-Anonymous-Id': ownerIdentity(), ...options.headers } });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'The request could not be completed.');
  return result;
}

function Answer({ content }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try { await navigator.clipboard.writeText(content); setCopied(true); setTimeout(() => setCopied(false), 1800); } catch { setCopied(false); }
  }
  return <div className="answer"><ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    <button className="icon-button copy" onClick={copy} aria-label="Copy answer">{copied ? <Check size={15} /> : <Copy size={15} />}</button></div>;
}

export default function Home() {
  const [chats, setChats] = useState([]);
  const [activeId, setActiveId] = useState('');
  const [ready, setReady] = useState(false);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [menu, setMenu] = useState(false);
  const [search, setSearch] = useState('');
  const [adminOpen, setAdminOpen] = useState(false);
  const [adminKey, setAdminKey] = useState('');
  const [authenticated, setAuthenticated] = useState(false);
  const [documents, setDocuments] = useState([]);
  const [category, setCategory] = useState('General');
  const [file, setFile] = useState(null);
  const [adminBusy, setAdminBusy] = useState(false);
  const [adminError, setAdminError] = useState('');
  const [notice, setNotice] = useState('');
  const bottom = useRef(null);
  const fileInput = useRef(null);
  const active = chats.find(chat => chat.id === activeId);
  const messages = active?.messages || [];

  useEffect(() => {
    let cancelled = false;
    async function restore() {
    let saved = [];
    try {
      const value = JSON.parse(localStorage.getItem('intelligence-chats') || '[]');
      if (Array.isArray(value)) saved = value.filter(chat => typeof chat.id === 'string' && typeof chat.title === 'string' && Array.isArray(chat.messages)).slice(0, 50);
    } catch { /* Browser storage may be unavailable. */ }
    try {
      const result = await request('conversations');
      const persisted = await Promise.all(result.conversations.map(async chat => {
        const data = await request(`conversations/${chat.id}`);
        return { ...chat, id: `saved-${chat.id}`, server_id: chat.id, messages: data.messages, next_before: data.next_before };
      }));
      saved = [...persisted, ...saved.filter(chat => !chat.server_id && chat.messages.length)].slice(0, 50);
    } catch (failure) { if (!cancelled) setError('Could not load server history. ' + failure.message); }
    if (cancelled) return;
    if (!saved.length) saved = [freshChat()];
    setChats(saved); setActiveId(saved[0].id); setReady(true);
    }
    restore();
    return () => { cancelled = true; };
  }, []);
  useEffect(() => { if (ready) { try { localStorage.setItem('intelligence-chats', JSON.stringify(chats.slice(0, 50))); } catch { /* Chat still works without local storage. */ } } }, [chats, ready]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages.length, busy]);

  function newChat() { if (busy) return; const chat = freshChat(); setChats(previous => [chat, ...previous].slice(0, 50)); setActiveId(chat.id); setInput(''); setError(''); setMenu(false); }
  async function deleteChat(id) {
    if (busy) return;
    const chat = chats.find(item => item.id === id);
    if (chat?.server_id) {
      setBusy(true);
      try { await request(`conversations/${chat.server_id}`, { method: 'DELETE' }); }
      catch (failure) { setError(failure.message); return; }
      finally { setBusy(false); }
    }
    const remaining = chats.filter(chat => chat.id !== id);
    if (!remaining.length) remaining.push(freshChat());
    setChats(remaining); if (id === activeId) setActiveId(remaining[0].id);
  }
  async function send(value = input) {
    const question = value.trim();
    if (!question || busy || !active) return;
    const id = active.id;
    setInput(''); setError(''); setBusy(true);
    setChats(previous => previous.map(chat => chat.id === id ? { ...chat, title: chat.messages.length ? chat.title : question.slice(0, 42), messages: [...chat.messages, { role: 'user', content: question }] } : chat));
    try {
      const result = await request('conversations/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question, conversation_id: active.server_id || null }) });
      setChats(previous => previous.map(chat => chat.id === id ? { ...chat, server_id: result.conversation_id, messages: [...chat.messages.slice(0, -1), ...result.messages] } : chat));
    } catch (failure) {
      setError(failure.message); setInput(question);
      setChats(previous => previous.map(chat => chat.id === id ? { ...chat, messages: chat.messages.slice(0, -1) } : chat));
    } finally { setBusy(false); }
  }
  async function loadOlder() {
    if (!active?.next_before || busy) return;
    setBusy(true); setError('');
    try {
      const result = await request(`conversations/${active.server_id}?before=${active.next_before}`);
      setChats(previous => previous.map(chat => chat.id === active.id ? { ...chat, messages: [...result.messages, ...chat.messages], next_before: result.next_before } : chat));
    } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  }
  async function loadDocuments() {
    const result = await request('admin/documents', { headers: { 'X-Admin-Key': adminKey } });
    setDocuments(result.documents); setAuthenticated(true);
  }
  async function login(event) {
    event.preventDefault(); setAdminBusy(true); setAdminError('');
    try { await loadDocuments(); } catch (failure) { setAdminError(failure.message); } finally { setAdminBusy(false); }
  }
  async function upload(event) {
    event.preventDefault(); if (!file) return; setAdminBusy(true); setAdminError(''); setNotice('');
    if (file.size > 4 * 1024 * 1024) { setAdminError('Website uploads are limited to 4 MB. Use Telegram for documents up to 20 MB.'); setAdminBusy(false); return; }
    const data = new FormData(); data.append('file', file); data.append('category', category);
    try {
      await request('admin/documents', { method: 'POST', headers: { 'X-Admin-Key': adminKey }, body: data });
      await loadDocuments(); setNotice('Document added. It is now available to website and Telegram questions.'); setFile(null); if (fileInput.current) fileInput.current.value = '';
    } catch (failure) { setAdminError(failure.message); } finally { setAdminBusy(false); }
  }
  async function removeDocument(document) {
    if (!window.confirm(`Delete ${document.name} from the shared knowledge base?`)) return;
    setAdminBusy(true); setAdminError(''); setNotice('');
    try { await request(`admin/documents/${document.id}`, { method: 'DELETE', headers: { 'X-Admin-Key': adminKey } }); await loadDocuments(); }
    catch (failure) { setAdminError(failure.message); } finally { setAdminBusy(false); }
  }
  function closeAdmin() { setAdminOpen(false); setAuthenticated(false); setAdminKey(''); setDocuments([]); setAdminError(''); setNotice(''); }

  return <div className="app-shell">
    {menu && <div className="sidebar-overlay" onClick={() => setMenu(false)} />}
    <aside className={`sidebar ${menu ? 'open' : ''}`}>
      <a className="brand" href="/"><span className="brand-mark"><Sparkles size={18} /></span>intelligence<span className="brand-dot">.</span></a>
      <button className="new-chat" disabled={busy || !ready} onClick={newChat}><Plus size={18} /> New conversation<span>↗</span></button>
      <label className="search"><Search size={15} /><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Search conversations" aria-label="Search conversations" /></label>
      <div className="sidebar-heading">YOUR CONVERSATIONS</div>
      <nav className="conversation-list">{chats.filter(chat => chat.title.toLowerCase().includes(search.toLowerCase())).map(chat => <div key={chat.id} className={`conversation ${chat.id === activeId ? 'selected' : ''}`}>
        <button disabled={busy} onClick={() => { setActiveId(chat.id); setMenu(false); setError(''); }}><MessageSquare size={15} /><span>{chat.title}</span></button>
        <button className="delete-chat" disabled={busy} onClick={() => deleteChat(chat.id)} aria-label={`Delete ${chat.title}`}><Trash2 size={13} /></button>
      </div>)}</nav>
      <div className="sidebar-bottom"><div className="telegram-card"><span className="telegram-logo">➤</span><div><strong>Answers, wherever you are</strong><p>Also available through your Telegram bot</p></div></div>
        <button className="admin-link" onClick={() => setAdminOpen(true)}><ShieldCheck size={16} /> Administrator <LockKeyhole size={13} /></button>
        <div className="profile"><div className="avatar">Y</div><div><strong>Your workspace</strong><p>History saved to your database</p></div></div>
      </div>
    </aside>
    <main className="main-panel">
      <header className="topbar"><div><button className="icon-button mobile-menu" onClick={() => setMenu(true)} aria-label="Open sidebar"><Menu size={21} /></button><span className="model-label">Intelligence <ChevronDown size={14} /></span><span className="model-badge">Document assistant</span></div><div className="private-label"><LockKeyhole size={13} /> Approved knowledge</div></header>
      <section className={`chat-area ${messages.length ? 'has-messages' : ''}`} aria-label="Conversation">
        {!messages.length ? <div className="welcome"><div className="welcome-mark"><Sparkles size={31} strokeWidth={1.4} /></div><div className="eyebrow">A LITTLE CLARITY GOES A LONG WAY</div><h1>What would you like<br />to understand?</h1><p>Explore your organization’s knowledge.<br />Ask a question, connect the details, find your answer.</p><div className="suggestions">{suggestions.map(([title, question], index) => <button disabled={!ready || busy} key={title} onClick={() => send(question)}><span className={`suggestion-icon color-${index}`}><BookOpen size={18} /></span><strong>{title}</strong><span>{question}</span></button>)}</div></div>
          : <div className="messages" aria-live="polite">{active?.next_before && <button className="primary-button" disabled={busy} onClick={loadOlder}>Load older messages</button>}{messages.map((message, index) => <div key={message.id || index} title={message.created_at || undefined} className={`message ${message.role}`}>{message.role === 'assistant' && <div className="assistant-mark"><Sparkles size={19} /></div>}{message.role === 'user' ? <div className="user-bubble">{message.content}</div> : <Answer content={message.content} />}</div>)}{busy && <div className="message assistant"><div className="assistant-mark"><Sparkles size={19} /></div><div className="thinking"><i /><i /><i /><span>Looking through the documents</span></div></div>}<div ref={bottom} /></div>}
      </section>
      <div className="composer-wrap">{error && <div className="error" role="alert">{error}</div>}<form className="composer" onSubmit={event => { event.preventDefault(); send(); }}><textarea value={input} maxLength={4000} onChange={event => setInput(event.target.value)} placeholder="Ask anything about the approved documents…" aria-label="Your question" rows={2} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }} /><div className="composer-bottom"><span><BookOpen size={14} /> Grounded in your documents</span><button disabled={busy || !input.trim() || !ready} className="send-button" aria-label="Send question"><ArrowUp size={20} /></button></div></form><p className="disclaimer">AI can make mistakes. Check important details against the original documents.</p></div>
    </main>
    {adminOpen && <div className="modal-backdrop"><section className="admin-modal" role="dialog" aria-modal="true" aria-labelledby="admin-title"><header><div><span className="eyebrow">ADMINISTRATOR WORKSPACE</span><h2 id="admin-title">Knowledge base</h2></div><button className="icon-button" aria-label="Close administrator panel" disabled={adminBusy} onClick={closeAdmin}><X size={20} /></button></header><p className="modal-description">Manage the documents that power both the website and Telegram. Only administrators can view this catalog.</p>
      {!authenticated ? <form className="admin-login" onSubmit={login}><label htmlFor="admin-key">Administrator key</label><input id="admin-key" type="password" autoComplete="off" value={adminKey} onChange={event => setAdminKey(event.target.value)} placeholder="Enter your ADMIN_API_KEY" required /><button className="primary-button" disabled={adminBusy}>{adminBusy ? 'Checking…' : 'Unlock knowledge base'}<LockKeyhole size={15} /></button><small>Your key is held only in memory for this panel.</small></form> : <><div className="admin-toolbar"><span><ShieldCheck size={16} /> Administrator access</span><button onClick={closeAdmin} disabled={adminBusy}><LogOut size={15} /> Sign out</button></div><form className="upload-form" onSubmit={upload}><label htmlFor="category">Document group</label><input id="category" maxLength={80} value={category} onChange={event => setCategory(event.target.value)} placeholder="e.g. Accounts" required /><label className="upload-zone"><Upload size={24} /><strong>{file?.name || 'Choose a document'}</strong><span>PDF, DOCX, XLSX, TXT, MD, CSV · up to 4 MB</span><input ref={fileInput} type="file" accept=".pdf,.docx,.xlsx,.txt,.md,.csv" onChange={event => setFile(event.target.files?.[0] || null)} /></label><button className="primary-button" disabled={adminBusy || !file}>{adminBusy ? 'Updating knowledge base…' : 'Add to knowledge base'}<Plus size={16} /></button></form><div className="document-groups">{[...new Set(documents.map(document => document.category))].map(group => <section key={group}><h3>{group}</h3>{documents.filter(document => document.category === group).map(document => <div className="document-row" key={document.id}><FileText size={19} /><div><strong>{document.name}</strong><small>Added {document.created_at}</small></div><button disabled={adminBusy} onClick={() => removeDocument(document)} className="icon-button" aria-label={`Delete ${document.name}`}><Trash2 size={16} /></button></div>)}</section>)}{!documents.length && <p className="empty-docs">No documents yet. Add your first document above.</p>}</div></>}{adminError && <div className="error" role="alert">{adminError}</div>}{notice && <div className="notice" role="status">{notice}</div>}
    </section></div>}
  </div>;
}
