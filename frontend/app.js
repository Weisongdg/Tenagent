/* 页面状态与 HTTP 调用。业务规则和统计全部由后端负责。 */
const $ = (selector) => document.querySelector(selector);
const state = { session: null, sessions: [], page: 1, total: 0, filters: {}, report: null, chatting: false };

async function api(path, body) {
  const response = await fetch(`/api${path}`, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '参数不正确，请检查输入。');
  return data;
}
function notice(text, error = false) {
  $('#notice').textContent = text;
  $('#notice').className = error ? 'error' : '';
  $('#notice').hidden = !text;
}
async function action(button, task) {
  button.disabled = true;
  notice('正在处理，请稍候…');
  try { await task(); } catch (error) { notice(error.message || '请求失败，请检查本地服务是否启动。', true); }
  finally { button.disabled = false; updatePagination(); }
}
function updatePagination() {
  $('#prev').disabled = state.page <= 1;
  $('#next').disabled = state.page * 25 >= state.total;
}
function markdown(element, text) {
  element.innerHTML = DOMPurify.sanitize(marked.parse(text), { FORBID_TAGS: ['img'] });
}
function addMessage(role, text) {
  const message = document.createElement('div');
  message.className = `message ${role}`;
  const avatar = document.createElement('div'); avatar.className = 'avatar'; avatar.textContent = role === 'user' ? '我' : 'T';
  const content = document.createElement('div'); content.className = 'message-content markdown';
  if (role === 'user') content.textContent = text; else markdown(content, text);
  message.append(avatar, content); $('#messages').append(message);
  $('#suggestions').hidden = true;
}
async function selectSession(id) {
  const messages = await api(`/sessions/${encodeURIComponent(id)}/messages`);
  state.session = id; localStorage.setItem('tenagent.session', id);
  $('#messages').replaceChildren(); $('#suggestions').hidden = messages.length > 0;
  messages.forEach((message) => addMessage(message.role, message.text));
  renderSessions();
}
function renderSessions() {
  $('#session-list').replaceChildren();
  [...state.sessions].reverse().forEach((session) => {
    const button = document.createElement('button'); button.textContent = session.title;
    button.className = session.thread_id === state.session ? 'selected' : '';
    button.disabled = state.chatting;
    button.onclick = () => action(button, async () => { await selectSession(session.thread_id); notice(''); });
    $('#session-list').append(button);
  });
}
async function loadSessions() {
  state.sessions = await api('/sessions');
  if (!state.sessions.length) state.sessions.push(await api('/sessions', { title: '默认会话' }));
  const saved = localStorage.getItem('tenagent.session');
  await selectSession(state.sessions.some((s) => s.thread_id === saved) ? saved : state.sessions.at(-1).thread_id);
}
$('#new-session').onsubmit = (event) => {
  event.preventDefault(); const form = event.currentTarget;
  action(form.querySelector('button'), async () => {
    const session = await api('/sessions', { title: form.elements.namedItem('title').value.trim() || '新会话' });
    state.sessions.push(session); await selectSession(session.thread_id); form.reset(); notice('');
  });
};
$('#chat-form').onsubmit = (event) => {
  event.preventDefault(); const form = event.currentTarget; const text = form.text.value.trim();
  if (!text || state.chatting || !state.session) return;
  const thread = state.session;
  action(form.querySelector('button'), async () => {
    state.chatting = true; form.text.disabled = true; $('#new-session button').disabled = true; renderSessions();
    addMessage('user', text); form.text.value = '';
    try { const result = await api(`/sessions/${thread}/messages`, { text }); addMessage('assistant', result.text); notice(''); }
    catch (error) {
      // 失败的轮次也可能已经持久化，以服务器历史为准，避免前端制造重复消息。
      try { await selectSession(thread); } catch { /* 保留原请求错误供用户查看 */ }
      throw error;
    } finally { state.chatting = false; form.text.disabled = false; $('#new-session button').disabled = false; renderSessions(); form.text.focus(); }
  });
};
$('#chat-form textarea').onkeydown = (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#chat-form').requestSubmit(); }
};
$('#suggestions').onclick = (event) => {
  if (event.target.tagName === 'BUTTON') { $('#chat-form textarea').value = event.target.textContent.trim(); $('#chat-form').requestSubmit(); }
};

function createFilters(id, label, submit) {
  const form = $(id);
  [['product', '产品'], ['area', '区域'], ['station', '工位'], ['status', '原始状态'], ['file_path', '来源文件路径'], ['sheet_name', '工作表']].forEach(([name, title]) => {
    const field = document.createElement('label'); field.textContent = title;
    const input = document.createElement('input'); input.name = name; input.placeholder = '全部'; field.append(input); form.append(field);
  });
  const button = document.createElement('button'); button.className = 'primary'; button.textContent = label; form.append(button);
  form.onsubmit = (event) => {
    event.preventDefault(); const filters = Object.fromEntries([...new FormData(form)].map(([key, value]) => [key, value.trim()]).filter(([, value]) => value));
    action(button, async () => { await submit(filters); notice(''); });
  };
}
async function loadIssues() {
  const result = await api('/issues/search', { filters: state.filters, offset: (state.page - 1) * 25, limit: 25 });
  state.total = result.total;
  $('#issue-count').textContent = result.total; $('#page-number').textContent = state.page;
  updatePagination();
  $('#issue-rows').replaceChildren();
  result.issues.forEach((item) => {
    const row = document.createElement('tr'); const issue = item.issue;
    [item.issue_no, issue.product, `${issue.area ?? '未填写'} / ${issue.station ?? '未填写'}`, issue.problem_description, issue.status, `${item.sheet_name} · 第 ${issue.source_row} 行`].forEach((text) => {
      const cell = document.createElement('td'); cell.textContent = text ?? '未填写'; row.append(cell);
    });
    row.lastChild.title = item.source_file; $('#issue-rows').append(row);
  });
  if (!result.issues.length) { const row = document.createElement('tr'); const cell = document.createElement('td'); cell.colSpan = 6; cell.textContent = '当前范围没有记录，可调整筛选或先导入 Excel。'; row.append(cell); $('#issue-rows').append(row); }
}
createFilters('#library-filter', '查询问题', async (filters) => { state.filters = filters; state.page = 1; await loadIssues(); });
$('#prev').onclick = () => action($('#prev'), async () => { state.page--; await loadIssues(); notice(''); $('#prev').disabled = state.page <= 1; });
$('#next').onclick = () => action($('#next'), async () => { state.page++; await loadIssues(); notice(''); });
$('#import-form').onsubmit = (event) => {
  event.preventDefault(); const form = event.currentTarget;
  action(form.querySelector('button'), async () => {
    const result = await api('/issues/import', Object.fromEntries(new FormData(form)));
    state.page = 1; await loadIssues(); notice(`已入库 ${result.total} 条：新增 ${result.inserted}，更新 ${result.updated}，未变化 ${result.skipped}。`);
  });
};
$('#sync').onclick = () => action($('#sync'), async () => { const result = await api('/vectors/sync', {}); notice(`已同步 ${result.indexed} 条记录。`); });
createFilters('#report-filter', '生成报告', async (filters) => {
  state.report = await api('/reports', filters); const result = state.report.report;
  $('#metrics').replaceChildren();
  [['问题总数', 'total'], ['已关闭', 'closed_count'], ['未关闭', 'open_count'], ['关闭情况待确认', 'uncertain_count']].forEach(([label, key]) => {
    const box = document.createElement('div'); box.className = 'metric'; box.textContent = label;
    const number = document.createElement('strong'); number.textContent = result[key]; box.append(number); $('#metrics').append(box);
  });
  $('#report-time').textContent = `生成于 ${result.generated_at} · 修改筛选后需重新生成`;
  markdown($('#report-content'), state.report.markdown); $('#report-result').hidden = false; $('#report-empty').hidden = true;
});
$('#download').onclick = () => {
  if (!state.report) return;
  const url = URL.createObjectURL(new Blob([state.report.markdown], { type: 'text/markdown;charset=utf-8' }));
  const link = document.createElement('a'); link.href = url; link.download = state.report.report.file_path.split(/[\\/]/).at(-1); link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
document.querySelectorAll('nav button').forEach((button) => {
  button.onclick = () => {
    document.querySelectorAll('.page').forEach((page) => { page.hidden = page.id !== button.dataset.page; });
    document.querySelectorAll('nav button').forEach((item) => item.classList.toggle('active', item === button));
    $('#breadcrumb').textContent = button.textContent.trim().slice(1).trim(); notice('');
    if (button.dataset.page === 'library') action(button, async () => { await loadIssues(); notice(''); });
  };
});
loadSessions().catch((error) => notice(error.message, true));
