import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const maxDuration = 300;

async function proxy(request, context) {
  const { path } = await context.params;
  const target = path.join('/');
  const method = request.method;
  const allowed = (target === 'chat' && method === 'POST') ||
    (target === 'conversations' && method === 'GET') ||
    (target === 'conversations/chat' && method === 'POST') ||
    (/^conversations\/(?:[1-9]\d*|[a-f0-9]{32})$/.test(target) && ['GET', 'DELETE'].includes(method)) ||
    (target === 'health' && method === 'GET') ||
    (target === 'admin/documents' && ['GET', 'POST'].includes(method)) ||
    (/^admin\/documents\/[a-f0-9]{32}$/.test(target) && method === 'DELETE');
  if (!allowed) return NextResponse.json({ detail: 'Not found.' }, { status: 404 });
  const headers = {};
  const key = request.headers.get('x-admin-key');
  if (key) headers['X-Admin-Key'] = key;
  const identity = request.headers.get('x-anonymous-id');
  if (identity) headers['X-Anonymous-Id'] = identity;
  const contentType = request.headers.get('content-type');
  if (contentType) headers['Content-Type'] = contentType;
  try {
    const body = ['POST', 'DELETE'].includes(method) ? await request.arrayBuffer() : undefined;
    if (body && body.byteLength > 21 * 1024 * 1024)
      return NextResponse.json({ detail: 'File too large.' }, { status: 413 });
    const query = new URL(request.url).search;
    const response = await fetch(`${process.env.BACKEND_URL || 'http://127.0.0.1:8000'}/${target}${query}`, {
      method, headers, body, cache: 'no-store', signal: AbortSignal.timeout(240000),
    });
    return new Response(await response.text(), { status: response.status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
  } catch {
    return NextResponse.json({ detail: 'Cannot reach the assistant. Start the Python backend and retry.' }, { status: 503 });
  }
}

export { proxy as GET, proxy as POST, proxy as DELETE };
