'use strict';

const $ = (selector) => document.querySelector(selector);
const categories = ['项目工作', '学习实践', '内容创作', '工具维护', '其他'];
const statuses = ['待开始', '准备中', '进行中', '待决定', '已完成', '暂缓'];
const fields = ['title', 'category', 'status', 'next', 'acceptance', 'url', 'artifact', 'note'];
let data = null;
let token = '';
let category = '';
let editId = null;
let busy = false;
let pendingCandidate = null;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function notify(message) {
  $('#message').textContent = message;
}

function updateButtons() {
  document.querySelectorAll('button').forEach((button) => {
    button.disabled = busy || !data;
  });
}

async function persist(candidate) {
  if (busy || !data) return false;
  busy = true;
  updateButtons();
  try {
    const response = await fetch('/api/workspace', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Workbench-Token': token },
      body: JSON.stringify(candidate),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || '保存失败');
    data = payload.data;
    pendingCandidate = null;
    notify('已保存到本地 · ' + new Date().toLocaleTimeString('zh-CN'));
    render();
    return true;
  } catch (error) {
    pendingCandidate = structuredClone(candidate);
    notify('未保存：' + error.message + '。可关闭编辑框后导出备份，保留最近一次保存草稿。');
    return false;
  } finally {
    busy = false;
    updateButtons();
  }
}

function render() {
  if (!data) return;
  const query = $('#search').value.trim().toLowerCase();
  const status = $('#status').value;
  const tasks = data.tasks.filter((task) =>
    (!category || task.category === category) &&
    (!status || task.status === status) &&
    (!query || [task.title, task.next, task.note, task.acceptance]
      .join(' ').toLowerCase().includes(query))
  );
  $('#count').textContent = tasks.length + ' 项任务';
  const container = $('#tasks');
  container.replaceChildren();
  if (!tasks.length) {
    const message = data.tasks.length
      ? '没有匹配的任务。试试调整搜索或筛选条件。'
      : '从一件正在做的事开始：新增任务，写下下一步和完成标准。';
    container.append(element('div', message, 'empty'));
  }
  for (const task of tasks) {
    const card = element('article', undefined, 'card');
    const top = element('div', undefined, 'cardtop');
    top.append(element('span', task.category), element('span', task.status, 'badge'));
    card.append(top, element('h3', task.title));
    const next = element('div', undefined, 'next');
    next.append(element('div', '下一步', 'small'), element('div', task.next || '尚未填写'));
    card.append(next);
    if (task.acceptance) card.append(element('div', '完成标准：' + task.acceptance, 'small'));
    const links = element('div', undefined, 'cardlinks');
    for (const [key, label] of [['url', '打开对话'], ['artifact', '查看成果']]) {
      if (!task[key]) continue;
      const link = element('a', label);
      link.href = task[key];
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      links.append(link);
    }
    const editButton = element('button', '编辑');
    editButton.onclick = () => edit(task);
    links.append(editButton);
    card.append(links);
    container.append(card);
  }
}

function edit(task) {
  editId = task ? task.id : null;
  const form = $('#form');
  form.reset();
  for (const field of fields) {
    const fallback = field === 'category' ? (category || categories[0])
      : field === 'status' ? statuses[0] : '';
    form.elements[field].value = task ? task[field] : fallback;
  }
  $('#remove').hidden = !task;
  $('#editor').showModal();
}

$('#form').onsubmit = async (event) => {
  event.preventDefault();
  const task = Object.fromEntries(new FormData(event.target));
  task.id = editId || crypto.randomUUID();
  const candidate = structuredClone(data);
  const index = candidate.tasks.findIndex((item) => item.id === editId);
  if (index < 0) candidate.tasks.push(task);
  else candidate.tasks[index] = task;
  if (await persist(candidate)) $('#editor').close();
};

$('#remove').onclick = async () => {
  if (!confirm('删除这个任务？保存前的数据会自动备份。')) return;
  const candidate = structuredClone(data);
  candidate.tasks = candidate.tasks.filter((task) => task.id !== editId);
  if (await persist(candidate)) $('#editor').close();
};
$('#close').onclick = () => $('#editor').close();
$('#add').onclick = () => edit();
$('#search').oninput = render;
$('#status').onchange = render;
$('#saveFocus').onclick = () => persist({ ...data, focus: $('#focus').value });
$('#focus').oninput = () => notify('重点已修改，点击“保存重点”写入本地');

$('#export').onclick = () => {
  const payload = structuredClone(pendingCandidate || data);
  payload.focus = $('#focus').value;
  // Match the compact request format so valid near-limit snapshots can be imported.
  const url = URL.createObjectURL(new Blob([JSON.stringify(payload)], {
    type: 'application/json',
  }));
  const link = element('a');
  link.href = url;
  link.download = 'chat-workbench-' + (pendingCandidate ? 'draft-' : '')
    + new Date().toISOString().slice(0, 10) + '.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

$('#import').onclick = () => $('#file').click();
$('#file').onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 5_000_000) throw new Error('文件超过 5 MB');
    const candidate = JSON.parse(await file.text());
    if (!candidate || !Array.isArray(candidate.tasks) || typeof candidate.focus !== 'string') {
      throw new Error('不是工作台备份');
    }
    if (!Number.isSafeInteger(candidate.revision) || candidate.revision < 0) {
      throw new Error('备份的版本号无效，当前数据未改变');
    }
    if (confirm('将用备份替换当前工作台，当前版本会自动备份。继续？')) {
      candidate.revision = data.revision;
      if (await persist(candidate)) $('#focus').value = data.focus;
    }
  } catch (error) {
    notify('导入失败：' + error.message);
  }
  event.target.value = '';
};

for (const name of ['', ...categories]) {
  const button = element('button', name || '全部任务', name ? '' : 'active');
  button.onclick = () => {
    category = name;
    document.querySelectorAll('nav button').forEach((node) => {
      node.classList.toggle('active', node === button);
    });
    render();
  };
  $('#categories').append(button);
}
for (const status of statuses) {
  $('#status').append(new Option(status, status));
  $('#form').elements.status.append(new Option(status, status));
}
for (const name of categories) {
  $('#form').elements.category.append(new Option(name, name));
}
$('#date').textContent = new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'full',
}).format(new Date());

updateButtons();
fetch('/api/workspace')
  .then(async (response) => {
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || '读取失败');
    return payload;
  })
  .then((payload) => {
    data = payload.data;
    token = payload.token;
    $('#focus').value = data.focus;
    render();
    updateButtons();
  })
  .catch((error) => notify('工作台未能加载：' + error.message));
