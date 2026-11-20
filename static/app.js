'use strict';
let state = {courses: [], tasks: [], lectures: []};
let view = 'dashboard', editing = null, deleting = null, activeLecture = null;
let calendarMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1), selectedCalendarDay = localIso(new Date());
let planSettings = {days: 14, minutes: 90}, activePlan = null;
function localIso(d) { return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; }
const filters = {search: '', course: '', status: 'all', kind: ''};
const $ = s => document.querySelector(s);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const localDate = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; };
const formatDate = d => new Date(d+'T12:00:00').toLocaleDateString(undefined, {month:'short',day:'numeric',year:'numeric'});
const course = id => state.courses.find(c => c.id === id) || {code:'Course',name:'',color:'#6663d5'};
const options = () => state.courses.map(c => `<option value="${c.id}">${escapeHTML(c.code)} · ${escapeHTML(c.name)}</option>`).join('');
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').classList.add('visible'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 4000); }
async function api(path, method='GET', body) {
  const init = {method};
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {init.body = JSON.stringify(body); init.headers = {'Content-Type':'application/json'};}
  const response = await fetch('/api'+path, init);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
  return data;
}
async function refresh() { state = await api('/state'); render(); }
function heading(title, subtitle, button='') { return `<div class="page-heading"><div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div>${button}</div>`; }
function empty(title, text, button='') { return `<div class="empty"><div class="empty-icon">◫</div><h3>${title}</h3><p>${text}</p>${button}</div>`; }
const newTaskButton = '<button class="button primary" data-action="new-task">＋ Add task</button>';
const exportTasksButton = '<a class="button secondary" href="/api/export/tasks.csv" download>↓ Export tasks CSV</a>'; 
function taskRow(t) {
  const c=course(t.course_id), late=!t.completed && t.due_date < localDate();
  return `<div class="task-row ${t.completed?'completed':''}"><input class="task-check" type="checkbox" ${t.completed?'checked':''} data-complete="${t.id}" aria-label="${escapeHTML('Mark '+t.title+' as '+(t.completed?'pending':'completed'))}"><div class="task-body"><span class="task-title">${escapeHTML(t.title)}</span><div class="task-meta"><span class="course-tag" style="--course-color:${c.color}">${escapeHTML(c.code)}</span><span>·</span><span>${t.kind}</span><span>·</span><span class="${late?'overdue':''}">${t.completed?'Completed':late?'Overdue · '+formatDate(t.due_date):'Due '+formatDate(t.due_date)}</span></div>${t.notes?`<div class="task-meta">${escapeHTML(t.notes)}</div>`:''}</div><span class="badge ${t.priority}">${t.priority}</span><div class="task-actions"><button class="icon-button" data-edit="${t.id}" aria-label="Edit ${escapeHTML(t.title)}" title="Edit task">✎</button><button class="icon-button" data-delete-task="${t.id}" aria-label="Delete ${escapeHTML(t.title)}" title="Delete task">×</button></div></div>`;
}
function courseRow(c) {const pending=state.tasks.filter(t=>t.course_id===c.id&&!t.completed).length; return `<a href="#tasks" class="course-row" data-course-filter="${c.id}"><span class="course-symbol" style="--course-color:${c.color}">▤</span><div><strong>${escapeHTML(c.code)}</strong><p>${escapeHTML(c.name)}</p></div><span class="count">${pending} pending</span></a>`;}
function overview() {
  const pending=state.tasks.filter(t=>!t.completed), completed=state.tasks.length-pending.length;
  const soon=pending.filter(t=>t.due_date>=localDate() && (new Date(t.due_date+'T12:00:00')-new Date(localDate()+'T12:00:00'))/86400000<=7).length;
  const percent=state.tasks.length?Math.round(completed/state.tasks.length*100):0;
  return heading('Your academic overview', 'A clear mind starts with a clear plan.', newTaskButton)+
  `<section class="hero"><div><div class="eyebrow">A LITTLE MORE ORGANIZED. A LOT MORE READY.</div><h2>Big goals. Small steps.</h2><p>Keep your coursework in focus and your deadlines in sight.<br>Let’s make today a productive one.</p></div><div class="hero-art" aria-hidden="true"><div class="art-paper"><i></i><i></i><i></i><i></i><i></i></div><span class="spark">✦</span><div class="art-check">✓</div></div></section>`+
  `<div class="stats">${[['Active courses',state.courses.length,'Your subjects this semester','▤'],['Pending tasks',pending.length,'One step at a time','◷'],['Completed tasks',completed,'Progress worth celebrating','✓'],['Due this week',soon,'Today and the next 7 days','▦']].map(([label,n,sub,icon])=>`<div class="stat"><div class="stat-top">${label}<span class="stat-icon">${icon}</span></div><div class="stat-value">${n}</div><small>${sub}</small></div>`).join('')}</div>`+
  `<div class="two-col"><div><section class="panel"><div class="panel-heading"><h2>Upcoming tasks <span class="muted">· ${pending.length}</span></h2><a href="#tasks" class="text-link">View all tasks →</a></div>${pending.length?pending.slice(0,5).map(taskRow).join(''):empty('You’re all caught up', 'Add an assignment or exam to plan your next step.',newTaskButton)}</section><section class="panel section-space"><div class="panel-heading"><h2>Your lecture library</h2><a class="text-link" href="#lectures">Open library →</a></div>${state.lectures.length?state.lectures.slice(0,2).map(l=>`<div class="course-row"><span class="course-symbol" style="--course-color:#c57564">▧</span><div><strong>${escapeHTML(l.filename)}</strong><p>${escapeHTML(course(l.course_id).code)} · ${l.pages} pages</p></div></div>`).join(''):empty('A home for your lecture notes', 'Upload a PDF and turn it into a short, source-linked summary.','<button class="button secondary" data-action="upload">Upload lecture</button>')}</section></div><div class="side-stack"><section class="panel"><div class="panel-heading"><h2>My courses</h2><button class="text-link" data-action="new-course">＋ Add</button></div>${state.courses.length?state.courses.slice(0,4).map(courseRow).join(''):empty('Start with a course','Add your subjects to organize tasks.','<button class="button secondary" data-action="new-course">Add course</button>')}</section><section class="progress-panel"><h2>Keep the momentum</h2><div class="progress-heading"><span>Overall task progress</span><strong>${percent}%</strong></div><div class="progress-track"><span style="width:${percent}%"></span></div><p>${completed} of ${state.tasks.length} tasks complete. Every small step counts.</p></section></div></div>`+
  (!state.courses.length?'<div class="info load-demo">Want to explore first? <button class="text-link" data-action="demo">Load sample courses and tasks →</button><br><small>Optional demo data. You can also start by adding your own courses.</small></div>':'');
}
function filteredTasks() {return state.tasks.filter(t => (!filters.search || (t.title+' '+t.notes+' '+course(t.course_id).code).toLowerCase().includes(filters.search.toLowerCase())) && (!filters.course || t.course_id===Number(filters.course)) && (filters.status==='all'||(filters.status==='completed'?!!t.completed:!t.completed)) && (!filters.kind||t.kind===filters.kind));}
function taskResults() {const tasks=filteredTasks(); return tasks.length?tasks.map(taskRow).join(''):empty('No tasks here yet','Add a task, or change your filters to see more.', newTaskButton);}
function tasksPage() {return heading('My tasks','A little structure for everything on your plate.',`<div class="heading-actions">${exportTasksButton}${newTaskButton}</div>`)+`<section class="panel"><div class="filters"><input id="search" aria-label="Search tasks" placeholder="Search tasks…" value="${escapeHTML(filters.search)}"><select id="filter-course" aria-label="Filter by course"><option value="">All courses</option>${options()}</select><select id="filter-status" aria-label="Filter by status"><option value="all">All statuses</option><option value="pending">Pending</option><option value="completed">Completed</option></select><select id="filter-kind" aria-label="Filter by type"><option value="">All types</option><option>Assignment</option><option>Exam</option></select></div><div id="task-results">${taskResults()}</div></section>`;}
function coursesPage() {return heading('My courses','Give every subject a space of its own.','<button class="button primary" data-action="new-course">＋ Add course</button>')+(state.courses.length?`<div class="course-grid">${state.courses.map(c=>{const tasks=state.tasks.filter(t=>t.course_id===c.id), done=tasks.filter(t=>t.completed).length;return `<article class="course-card"><span class="course-symbol" style="--course-color:${c.color}">▤</span><h2>${escapeHTML(c.code)}</h2><p>${escapeHTML(c.name)}</p><div class="progress-heading"><span>${done} of ${tasks.length} tasks completed</span><strong>${tasks.length?Math.round(done/tasks.length*100):0}%</strong></div><div class="progress-track"><span style="width:${tasks.length?done/tasks.length*100:0}%"></span></div><button class="button secondary" data-course-filter="${c.id}">View course tasks →</button></article>`;}).join('')}</div>`:`<section class="panel">${empty('Build your semester','Add your first course to get started.','<button class="button primary" data-action="new-course">Add course</button>')}</section>`);}
function lecturesPage() {
  return heading('Lecture library','Less searching. More understanding.','<button class="button primary" data-action="upload">＋ Upload lecture</button>')+
  `<div class="info">✦ <strong>Local lecture study tools · Sprint 2</strong><br>Upload instructor-provided text-based PDFs and get source-linked extracts, relevant passages and practice questions. Everything runs locally; no external AI service is used.</div>`+
  (state.lectures.length?`<div class="lecture-grid">${state.lectures.map(l=>`<article class="lecture-card"><span class="pdf-icon">PDF</span><h3>${escapeHTML(l.filename)}</h3><p><span class="course-tag" style="--course-color:${course(l.course_id).color}">${escapeHTML(course(l.course_id).code)}</span> · ${l.pages} pages</p><div class="lecture-actions"><button class="button primary" data-summary="${l.id}">${l.summary?'View summary':'Make summary'}</button><button class="button secondary" data-ask="${l.id}">Ask lecture</button><button class="button secondary" data-quiz="${l.id}">Practice</button><a class="button secondary" href="/api/lectures/${l.id}/file" target="_blank" rel="noopener">Open PDF ↗</a><button class="icon-button" data-delete-lecture="${l.id}" aria-label="Delete ${escapeHTML(l.filename)}">×</button></div><div class="status-note">${l.summary?'✓ Summary saved in your workspace':'Ready for your next study session'}</div></article>`).join('')}</div>`:`<section class="panel">${empty('Your next study session starts here','Upload a text-based lecture PDF to summarize, search and practice.','<button class="button primary" data-action="upload">Upload your first lecture</button>')}</section>`);
}
function calendarPage() {
  const year = calendarMonth.getFullYear(), month = calendarMonth.getMonth();
  const first = new Date(year,month,1), weekday = (first.getDay()+6)%7;
  const daysInMonth = new Date(year,month+1,0).getDate();
  const cells = Math.ceil((weekday+daysInMonth)/7)*7;
  const label = first.toLocaleDateString(undefined,{month:'long',year:'numeric'});
  let grid = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(x=>`<div class="calendar-weekday">${x}</div>`).join('');
  for(let i=0;i<cells;i++) {
    const day = new Date(year,month,1+i-weekday), iso=localIso(day);
    const matches = state.tasks.filter(t=>t.due_date===iso);
    grid += `<button class="calendar-cell ${day.getMonth()===month?'':'other-month'} ${iso===localDate()?'today-cell':''} ${selectedCalendarDay===iso?'selected':''}" data-cal-day="${iso}" aria-label="${formatDate(iso)}, ${matches.length} task(s)" ${selectedCalendarDay===iso?'aria-pressed="true"':''}><span class="day-number">${day.getDate()}</span><span class="calendar-events">${matches.slice(0,3).map(t=>`<span class="calendar-pill ${t.completed?'is-done':''}" style="--course-color:${course(t.course_id).color}" title="${escapeHTML(t.title)}">${escapeHTML(t.title)}</span>`).join('')}${matches.length>3?`<small>+${matches.length-3} more</small>`:''}</span></button>`;
  }
  const selected = state.tasks.filter(t=>t.due_date===selectedCalendarDay);
  return heading('Academic calendar','See all upcoming assignments and exams in one place.',newTaskButton)+
  `<div class="calendar-layout"><section class="panel calendar-panel"><div class="calendar-toolbar"><h2>${escapeHTML(label)}</h2><div class="heading-actions"><button class="button secondary" data-cal-today>Today</button><button class="button secondary calendar-arrow" data-cal-nav="-1" aria-label="Previous month">←</button><button class="button secondary calendar-arrow" data-cal-nav="1" aria-label="Next month">→</button></div></div><div class="calendar-grid">${grid}</div></section><section class="panel calendar-agenda"><div class="panel-heading"><h2>${formatDate(selectedCalendarDay)}</h2><span class="muted">${selected.length} event${selected.length===1?'':'s'}</span></div>${selected.length?selected.map(taskRow).join(''):empty('No deadlines on this day','Select another date, or add an assignment or exam.',newTaskButton)}</section></div>`;
}
function plannerPage() {
  return heading('Study planner','A realistic starting point for planning your revision.',newTaskButton)+
  `<div class="info">✦ This is a suggested schedule, calculated from <strong>pending tasks, due dates, and priorities</strong>. Each block is 30 minutes. It does not automatically know the actual time you need or track completed sessions.</div>`+
  `<div class="panel planner-controls"><label>Plan period<select id="plan-days"><option value="7">Next 7 days</option><option value="14">Next 14 days</option><option value="21">Next 21 days</option></select></label><label>Daily study time<select id="plan-minutes"><option value="60">1 hour</option><option value="90">1.5 hours</option><option value="120">2 hours</option><option value="180">3 hours</option></select></label><div class="planner-download"><a class="button secondary" id="export-plan" href="/api/export/study-plan.csv?days=${planSettings.days}&minutes=${planSettings.minutes}" download>↓ Export study plan CSV</a></div></div><div id="study-plan-content" aria-live="polite"><section class="panel"><p class="loading-note">Preparing your schedule…</p></section></div>`;
}
function drawPlan(plan) {
  const target=$('#study-plan-content'); if(!target || view!=='planner') return;
  if(!plan.sessions.length) {target.innerHTML=`<section class="panel">${empty('No study blocks planned','Add pending tasks due within the selected period, or check whether all tasks are completed.',newTaskButton)}</section>`;return;}
  const days=[...new Set(plan.sessions.map(s=>s.date))];
  target.innerHTML=`<div class="plan-summary"><div class="plan-stat"><strong>${plan.sessions.length}</strong><span>30-minute blocks</span></div><div class="plan-stat"><strong>${Math.round(plan.total_minutes/60*10)/10}h</strong><span>Total suggested study time</span></div><div class="plan-stat"><strong>${days.length}</strong><span>Scheduled days</span></div></div>${plan.unscheduled.length?`<div class="warning-note"><strong>Some study blocks did not fit before the deadlines.</strong><br>${plan.unscheduled.map(t=>`${escapeHTML(t.title)}: ${t.unscheduled_minutes} minutes`).join('<br>')}</div>`:''}<div class="plan-days">${days.map(day=>`<section class="plan-day panel"><div class="panel-heading"><h2>${formatDate(day)}</h2><span class="muted">${plan.sessions.filter(s=>s.date===day).length*30} minutes</span></div>${plan.sessions.filter(s=>s.date===day).map(s=>`<div class="study-session"><span class="session-duration">30 min</span><div><strong>${escapeHTML(s.title)}</strong><div class="task-meta">${escapeHTML(s.course)} · ${escapeHTML(s.kind)} · ${escapeHTML(s.priority)} priority · Due ${formatDate(s.due_date)} ${s.overdue?'· Overdue':''}</div></div></div>`).join('')}</section>`).join('')}</div>`;
}
async function loadPlan() {
  if(view!=='planner')return;
  $('#plan-days').value=String(planSettings.days);$('#plan-minutes').value=String(planSettings.minutes);
  $('#export-plan').href=`/api/export/study-plan.csv?days=${planSettings.days}&minutes=${planSettings.minutes}`;
  try {
    activePlan=await api(`/study-plan?days=${planSettings.days}&minutes=${planSettings.minutes}`);
    drawPlan(activePlan);
  } catch(e){const panel=$('#study-plan-content');if(panel)panel.innerHTML=`<div class="warning-note">${escapeHTML(e.message)}</div>`;}
}
function render() {
  view = location.hash.slice(1) || 'dashboard';
  if (!['dashboard','tasks','courses','lectures','calendar','planner'].includes(view)) view='dashboard';
  const pages={dashboard:overview,tasks:tasksPage,courses:coursesPage,lectures:lecturesPage,calendar:calendarPage,planner:plannerPage};
  $('#main').innerHTML=pages[view]();
  $('#breadcrumb').textContent={dashboard:'Overview',tasks:'My tasks',courses:'Courses',lectures:'Lecture library',calendar:'Calendar',planner:'Study planner'}[view];
  document.querySelectorAll('nav a').forEach(a=>{a.classList.toggle('active',a.dataset.view===view);if(a.dataset.view===view)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  if(view==='tasks') {$('#filter-course').value=filters.course;$('#filter-status').value=filters.status;$('#filter-kind').value=filters.kind;}
  if(view==='planner')loadPlan();
}
function showDialog(id) {const d=$('#'+id);const error=d.querySelector('.form-error');if(error)error.textContent='';d.showModal();}
function openTask(id) {
  if(!state.courses.length){toast('Add a course first, then create your task.');return openCourse();}
  editing=id||null;const form=$('#task-form');form.reset();form.elements.course_id.innerHTML=options();
  const task=state.tasks.find(t=>t.id===id);
  $('#task-dialog-title').textContent=task?'Edit task':'New task';
  if(task) for(const key of ['title','course_id','kind','due_date','priority','notes']) form.elements[key].value=task[key];
  else {form.elements.due_date.value=view==='calendar'?selectedCalendarDay:localDate();if(filters.course)form.elements.course_id.value=filters.course;}
  showDialog('task-dialog');
}
function openCourse() {$('#course-form').reset();showDialog('course-dialog');}
function openUpload() {if(!state.courses.length){toast('Add a course before uploading a lecture.');return openCourse();}$('#upload-form').reset();$('#upload-form').elements.course_id.innerHTML=options();showDialog('upload-dialog');}
async function submit(form, job, dialog, message) {const button=form.querySelector('[type=submit]');button.disabled=true;const label=button.textContent;button.textContent='Saving…';form.querySelector('.form-error').textContent='';try {await job();$('#'+dialog).close();await refresh();toast(message);}catch(e){form.querySelector('.form-error').textContent=e.message;}finally{button.disabled=false;button.textContent=label;}}
$('#task-form').addEventListener('submit', e=>{e.preventDefault();const form=e.currentTarget, body=Object.fromEntries(new FormData(form));body.course_id=Number(body.course_id);body.completed=editing?!!state.tasks.find(t=>t.id===editing).completed:false;submit(form,()=>api(editing?'/tasks/'+editing:'/tasks',editing?'PUT':'POST',body),'task-dialog','Task saved.');});
$('#course-form').addEventListener('submit',e=>{e.preventDefault();const form=e.currentTarget;submit(form,()=>api('/courses','POST',Object.fromEntries(new FormData(form))),'course-dialog','Course added. You’re ready to plan.');});
$('#upload-form').addEventListener('submit',e=>{e.preventDefault();const form=e.currentTarget;submit(form,()=>api('/lectures','POST',new FormData(form)),'upload-dialog','Lecture uploaded. Open the library to summarize it.');});
async function showSummary(id, button) {const lecture=state.lectures.find(l=>l.id===id);const text=button.textContent;button.disabled=true;button.textContent='Extracting…';try {let summary=lecture.summary?JSON.parse(lecture.summary):(await api(`/lectures/${id}/summary`,'POST',{})).summary;await refresh();$('#summary-title').textContent=lecture.filename;$('#summary-content').innerHTML=summary.map(b=>`<div class="summary-bullet"><p>${escapeHTML(b.text)}</p><a href="/api/lectures/${id}/file#page=${b.page}" target="_blank" rel="noopener">View source · Page ${b.page} ↗</a></div>`).join('');showDialog('summary-dialog');}catch(e){toast(e.message);}finally{button.disabled=false;button.textContent=text;}}
function openQA(id) {
  const lecture=state.lectures.find(l=>l.id===id);if(!lecture)return;
  activeLecture=id;$('#qa-title').textContent=`Ask · ${lecture.filename}`;
  $('#qa-form').reset();$('#qa-form').querySelector('.form-error').textContent='';
  $('#qa-results').innerHTML='';showDialog('qa-dialog');
}
$('#qa-form').addEventListener('submit',async e=>{
  e.preventDefault();const form=e.currentTarget, button=form.querySelector('[type=submit]');
  const question=form.elements.question.value.trim();
  button.disabled=true;button.textContent='Searching lecture…';form.querySelector('.form-error').textContent='';
  try {
    const answer=await api(`/lectures/${activeLecture}/ask`,'POST',{question});
    $('#qa-results').innerHTML=`<section class="qa-answer"><h3>${answer.found?'Relevant lecture passages':'No matching passage found'}</h3><p>${escapeHTML(answer.answer)}</p>${answer.sources.map(item=>`<div class="summary-bullet"><p>${escapeHTML(item.text)}</p><a href="/api/lectures/${activeLecture}/file#page=${item.page}" target="_blank" rel="noopener">View source · Page ${item.page} ↗</a></div>`).join('')}</section>`;
  }catch(error){form.querySelector('.form-error').textContent=error.message;}
  finally{button.disabled=false;button.textContent='Find lecture answer';}
});
async function openQuiz(id,button) {
  const lecture=state.lectures.find(l=>l.id===id);if(!lecture)return;
  const original=button.textContent;button.disabled=true;button.textContent='Preparing…';
  try {
    const response=await api(`/lectures/${id}/questions`);
    $('#quiz-title').textContent=`Practice · ${lecture.filename}`;
    $('#quiz-content').innerHTML=response.questions.length?response.questions.map((q,i)=>`<section class="practice-card"><div class="eyebrow">QUESTION ${i+1} · ${escapeHTML(q.type)}</div><p>${escapeHTML(q.question)}</p><button class="button secondary" data-reveal="${i}">Show answer</button><div class="practice-answer" id="practice-answer-${i}" hidden><strong>Answer: </strong>${escapeHTML(q.answer)}<br><a href="/api/lectures/${id}/file#page=${q.page}" target="_blank" rel="noopener">View lecture · Page ${q.page} ↗</a></div></section>`).join(''):empty('Not enough readable sentences','Try another text-based PDF with complete sentences and definitions.');
    showDialog('quiz-dialog');
  }catch(e){toast(e.message);}finally{button.disabled=false;button.textContent=original;}
}
function confirmDelete(type,id) {deleting={type,id};$('#confirm-title').textContent=`Delete this ${type==='tasks'?'task':'lecture'}?`;showDialog('confirm-dialog');}
$('#confirm-delete').addEventListener('click',async e=>{e.target.disabled=true;try{await api(`/${deleting.type}/${deleting.id}`,'DELETE');$('#confirm-dialog').close();await refresh();toast('Item deleted.');}catch(err){toast(err.message);}finally{e.target.disabled=false;}});
document.addEventListener('click', async e=>{
  const close=e.target.closest('[data-close]');if(close){close.closest('dialog').close();return;}
  const b=e.target.closest('button,[data-course-filter]');if(!b)return;
  try {
    if(b.dataset.action==='new-task')openTask();
    if(b.dataset.action==='new-course')openCourse();
    if(b.dataset.action==='upload')openUpload();
    if(b.dataset.action==='demo'){b.disabled=true;await api('/demo','POST',{});await refresh();toast('Sample workspace loaded.');}
    if(b.dataset.edit)openTask(Number(b.dataset.edit));
    if(b.dataset.deleteTask)confirmDelete('tasks',Number(b.dataset.deleteTask));
    if(b.dataset.deleteLecture)confirmDelete('lectures',Number(b.dataset.deleteLecture));
    if(b.dataset.summary)await showSummary(Number(b.dataset.summary),b);
    if(b.dataset.ask)openQA(Number(b.dataset.ask));
    if(b.dataset.quiz)await openQuiz(Number(b.dataset.quiz),b);
    if(b.hasAttribute('data-reveal')){const answer=$('#practice-answer-'+b.dataset.reveal);answer.hidden=!answer.hidden;b.textContent=answer.hidden?'Show answer':'Hide answer';}
    if(b.hasAttribute('data-cal-nav')){calendarMonth=new Date(calendarMonth.getFullYear(),calendarMonth.getMonth()+Number(b.dataset.calNav),1);render();}
    if(b.hasAttribute('data-cal-today')){calendarMonth=new Date(new Date().getFullYear(),new Date().getMonth(),1);selectedCalendarDay=localDate();render();}
    if(b.dataset.calDay){selectedCalendarDay=b.dataset.calDay;calendarMonth=new Date(b.dataset.calDay+'T12:00:00');calendarMonth.setDate(1);render();}
    if(b.dataset.courseFilter){filters.course=b.dataset.courseFilter;filters.status='all';filters.search='';filters.kind='';if(location.hash==='#tasks')render();else location.hash='tasks';}
  }catch(err){toast(err.message);b.disabled=false;}
});
document.addEventListener('change',async e=>{
  if(e.target.matches('[data-complete]')){const input=e.target, t=state.tasks.find(t=>t.id===Number(input.dataset.complete));input.disabled=true;try{await api('/tasks/'+t.id,'PUT',{...t,completed:input.checked});await refresh();toast(input.checked?'Task completed. Nice work!':'Task moved back to pending.');}catch(err){input.checked=!!t.completed;input.disabled=false;toast(err.message);}}
  const key={'filter-course':'course','filter-status':'status','filter-kind':'kind'}[e.target.id];
  if(key){filters[key]=e.target.value;$('#task-results').innerHTML=taskResults();}
  if(e.target.id==='plan-days'||e.target.id==='plan-minutes'){planSettings[e.target.id==='plan-days'?'days':'minutes']=Number(e.target.value);loadPlan();}
});
document.addEventListener('input',e=>{if(e.target.id==='search'){filters.search=e.target.value;$('#task-results').innerHTML=taskResults();}});
window.addEventListener('hashchange',render);
$('#today').textContent=new Date().toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric',year:'numeric'});
refresh().catch(e=>{$('#main').innerHTML=empty('Could not load your workspace',escapeHTML(e.message),'<button class="button primary" onclick="location.reload()">Try again</button>');});
