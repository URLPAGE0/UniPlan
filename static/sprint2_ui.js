'use strict';
/* SW332 planned Sprint 2: PBI-16..21. All data is kept in user-scoped server APIs. */
let noteLectureId = null, bookmarkLectureId = null, editingSession = null, quizLectureId = null;
let lectureSearchTimer;

const oldOverview = overview;
overview = function() {
  return oldOverview() + `<section class="panel section-space" id="reminder-panel"><div class="panel-heading"><h2>🔔 Deadline reminders · PBI-16</h2><span class="muted">Visible while UniPlan is open</span></div><div id="reminder-results" class="saved-list"><p class="muted">Checking deadlines...</p></div></section>`;
};

function savedPage() {
  return heading('Saved study materials','Your lecture notes and page bookmarks in one place.') +
    `<div class="saved-layout"><section class="panel"><div class="panel-heading"><h2>Lecture notes · PBI-18</h2></div><div id="all-notes" class="saved-list">Loading notes...</div></section>
    <section class="panel"><div class="panel-heading"><h2>Bookmarked pages · PBI-21</h2></div><div id="all-bookmarks" class="saved-list">Loading bookmarks...</div></section></div>`;
}
function progressPage() {
  return heading('Study progress','Review your practice results and topics that need attention.') +
    `<section class="panel"><div class="panel-heading"><h2>Recent quiz results · PBI-19</h2></div><div id="quiz-progress" class="saved-list">Loading quiz results...</div></section>
     <section class="panel section-space"><div class="panel-heading"><h2>Topics to review · PBI-15</h2></div><div id="review-topics" class="saved-list">Checking your answers...</div></section>`;
}

const oldRender = render;
render = function() {
  oldRender();
  if (view === 'dashboard') loadReminders();
  if (view === 'saved') loadSavedMaterials();
  if (view === 'progress') loadStudyProgress();
};

async function loadReminders() {
  try {
    const {reminders} = await api('/reminders');
    const el = $('#reminder-results');
    if (!el || view !== 'dashboard') return;
    el.innerHTML = reminders.length ? reminders.map(r => `<div class="saved-entry"><div>
        <strong>${escapeHTML(r.title)}</strong><p>${escapeHTML(r.course)} · ${escapeHTML(r.kind)} · ${formatDate(r.due_date)}</p></div>
        <span class="reminder-status ${r.days_left < 0 ? 'is-overdue' : ''}">${escapeHTML(r.message)}</span></div>`).join('') :
      `<p class="muted">No overdue tasks or deadlines in the next 3 days. Reminders refresh when you open the dashboard.</p>`;
  } catch(e) {if($('#reminder-results')) $('#reminder-results').textContent=e.message;}
}

function noteMarkup(note, fromAll = false) {
  return `<article class="saved-entry"><div><strong>${escapeHTML(note.title)}</strong><p>${escapeHTML(note.body)}</p>
    <small class="muted">${fromAll ? escapeHTML(note.filename)+' · ':''}Page ${note.page}</small></div>
    <div class="saved-actions"><a class="button secondary" href="/api/lectures/${note.lecture_id}/file#page=${note.page}" target="_blank" rel="noopener">Open page ↗</a>
    <button class="button secondary" data-remove-note="${note.id}">Delete</button></div></article>`;
}
function bookmarkMarkup(item, fromAll = false) {
  return `<article class="saved-entry"><div><strong>${escapeHTML(item.label)}</strong><p>${fromAll ? escapeHTML(item.filename)+' · ':''}Page ${item.page}</p></div>
    <div class="saved-actions"><a class="button secondary" href="/api/lectures/${item.lecture_id}/file#page=${item.page}" target="_blank" rel="noopener">Open page ↗</a>
    <button class="button secondary" data-remove-bookmark="${item.id}">Delete</button></div></article>`;
}
async function loadSavedMaterials() {
  try {
    const result = await api('/saved');
    if(view !== 'saved' || !$('#all-notes')) return;
    $('#all-notes').innerHTML = result.notes.length ? result.notes.map(n=>noteMarkup(n,true)).join('') : '<p class="muted">No lecture notes saved yet. Open a lecture and select Notes.</p>';
    $('#all-bookmarks').innerHTML = result.bookmarks.length ? result.bookmarks.map(b=>bookmarkMarkup(b,true)).join('') : '<p class="muted">No pages bookmarked yet. Open a lecture and select ☆ Pages.</p>';
  } catch(e) {if($('#all-notes')) $('#all-notes').textContent=e.message;}
}
async function loadStudyProgress() {
  try {
    const p = await api('/progress');
    if(view !== 'progress' || !$('#quiz-progress')) return;
    $('#quiz-progress').innerHTML = `<p class="muted">Academic tasks completed: ${p.completed_tasks} of ${p.total_tasks}</p>`+
      (p.attempts.length ? p.attempts.map(a=>`<article class="saved-entry"><div><strong>${escapeHTML(a.filename)}</strong>
       <p>${new Date(a.created_at*1000).toLocaleDateString()}</p></div><span class="score-pill">${a.score} / ${a.total} (${Math.round(100*a.score/a.total)}%)</span></article>`).join('') : '<p class="muted">No submitted quizzes yet. Open a lecture and choose Practice.</p>');
    $('#review-topics').innerHTML = p.needs_review.length ? p.needs_review.map(x=>`<article class="saved-entry"><div>
       <strong>${escapeHTML(x.lecture)}</strong><p>${escapeHTML(x.question)}</p><small class="muted">Review lecture page ${x.page}</small></div>
       <a class="button secondary" href="/api/lectures/${x.lecture_id}/file#page=${x.page}" target="_blank" rel="noopener">Review source ↗</a></article>`).join('') :
       '<p class="muted">No missed quiz questions recorded. Take a practice quiz to identify topics that need review.</p>';
  } catch(e) {if($('#quiz-progress')) $('#quiz-progress').textContent=e.message;}
}

async function openNotes(id) {
  const lecture=state.lectures.find(l=>l.id===id);if(!lecture) return;
  noteLectureId=id;
  $('#note-dialog-title').textContent=`Notes · ${lecture.filename}`;
  $('#note-form').reset();$('#note-form').elements.page.max=String(lecture.pages);
  await reloadLectureNotes();showDialog('note-dialog');
}
async function reloadLectureNotes() {
  const data=await api(`/lectures/${noteLectureId}/notes`);
  $('#note-list').innerHTML=data.notes.length?data.notes.map(n=>noteMarkup(n)).join(''):'<p class="muted">No saved notes for this lecture yet.</p>';
}
$('#note-form').addEventListener('submit', async event=>{
  event.preventDefault();const form=event.currentTarget, button=form.querySelector('[type=submit]');button.disabled=true;
  try {
    await api(`/lectures/${noteLectureId}/notes`,'POST',{title:form.elements.title.value,body:form.elements.body.value,page:Number(form.elements.page.value)});
    form.reset();await reloadLectureNotes();toast('Lecture note saved.');
  } catch(e) {form.querySelector('.form-error').textContent=e.message;} finally {button.disabled=false;}
});

async function openBookmarks(id) {
  const lecture=state.lectures.find(l=>l.id===id);if(!lecture)return;
  bookmarkLectureId=id;$('#bookmark-dialog-title').textContent=`Bookmarks · ${lecture.filename}`;
  $('#bookmark-form').reset();$('#bookmark-form').elements.page.max=String(lecture.pages);
  await reloadBookmarks();showDialog('bookmark-dialog');
}
async function reloadBookmarks() {
  const data=await api(`/lectures/${bookmarkLectureId}/bookmarks`);
  $('#bookmark-list').innerHTML=data.bookmarks.length?data.bookmarks.map(b=>bookmarkMarkup(b)).join(''):'<p class="muted">No pages bookmarked in this lecture.</p>';
}
$('#bookmark-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.currentTarget,button=form.querySelector('[type=submit]');button.disabled=true;
  try {
    const r=await api(`/lectures/${bookmarkLectureId}/bookmarks`,'POST',{page:Number(form.elements.page.value),label:form.elements.label.value});
    await reloadBookmarks();toast(r.saved?'Bookmark saved.':'Page was already bookmarked.');
  } catch(e) {form.querySelector('.form-error').textContent=e.message;} finally {button.disabled=false;}
});

function editStudySession(taskId, number) {
  if(!activePlan) return;
  const s=activePlan.sessions.find(x=>x.task_id===taskId&&x.block_number===number);
  if(!s)return;
  editingSession={taskId,number};
  $('#session-form').elements.date.value=s.date;
  $('#session-form').elements.time.value=s.start_time;
  $('#reset-session').hidden=!s.customized;
  showDialog('session-dialog');
}
$('#session-form').addEventListener('submit', async event=>{
  event.preventDefault();const f=event.currentTarget,b=f.querySelector('[type=submit]');b.disabled=true;
  try {
    await api(`/study-sessions/${editingSession.taskId}/${editingSession.number}`,'PUT',{date:f.elements.date.value,time:f.elements.time.value});
    $('#session-dialog').close();await loadPlan();toast('Study session rescheduled and saved.');
  } catch(e) {f.querySelector('.form-error').textContent=e.message;}finally{b.disabled=false;}
});
$('#reset-session').addEventListener('click',async ()=>{
  if(!editingSession)return;
  try {
    await api(`/study-sessions/${editingSession.taskId}/${editingSession.number}`,'DELETE');
    $('#session-dialog').close();await loadPlan();toast('Suggested session restored.');
  } catch(e){toast(e.message);}
});

// A practice attempt is submitted and scored, then saved to the student's account.
openQuiz = async function(id,button) {
  const lecture=state.lectures.find(l=>l.id===id);if(!lecture)return;
  const previous=button.textContent;button.disabled=true;button.textContent='Preparing…';
  try {
    quizLectureId=id;
    const [{questions},saved]=await Promise.all([api(`/lectures/${id}/questions`),api(`/lectures/${id}/quiz-attempts`)]);
    $('#quiz-title').textContent=`Practice · ${lecture.filename}`;
    const fields=questions.map((q,i)=>`<section class="practice-card"><div class="eyebrow">QUESTION ${i+1} · ${escapeHTML(q.type)}</div>
       <p>${escapeHTML(q.question)}</p><label>Your answer<input class="quiz-input" name="answer-${i}" maxlength="2000" placeholder="Type your answer"></label>
       <small class="muted">Reference page: ${q.page}</small></section>`).join('');
    $('#quiz-content').innerHTML=questions.length ?
      `<p class="muted">Answers are checked against lecture-derived text. Equivalent wording may be marked incorrect; always review the source.</p>
       <form id="scored-quiz-form">${fields}<p class="form-error" role="alert"></p><button class="button primary" type="submit">Submit quiz and see score</button></form><div id="scored-quiz-result"></div><div id="quiz-history"></div>` :
      empty('Not enough lecture content','Try another text-based PDF with definitions or sentences.');
    const history=$('#quiz-history'); if(history)history.innerHTML=saved.attempts.length?`<h3 class="section-space">Previous results</h3>`+saved.attempts.map(x=>`<p class="muted">${new Date(x.created_at*1000).toLocaleDateString()}: ${x.score} of ${x.total} correct</p>`).join(''):'';
    showDialog('quiz-dialog');
  }catch(e){toast(e.message);} finally{button.disabled=false;button.textContent=previous;}
};
document.addEventListener('submit',async e=>{
  if(e.target.id !== 'scored-quiz-form')return;
  e.preventDefault();const f=e.target,inputs=[...f.querySelectorAll('input.quiz-input')],button=f.querySelector('[type=submit]');button.disabled=true;
  try {
    const r=await api(`/lectures/${quizLectureId}/quiz-attempts`,'POST',{answers:inputs.map(x=>x.value)});
    $('#scored-quiz-result').innerHTML=`<div class="quiz-score"><strong>Your score: ${r.score}/${r.total} (${Math.round(100*r.score/r.total)}%)</strong><p>Answers and source pages</p></div>`+
      r.review.map((q,i)=>`<div class="saved-entry"><div><strong>${q.correct?'✓ Correct':'○ Needs review'} — Question ${i+1}</strong><p>${escapeHTML(q.question)}</p>
      <p><b>Correct answer:</b> ${escapeHTML(q.answer)}<br><b>Your answer:</b> ${escapeHTML(q.submitted || '(blank)')}</p>
      <a href="/api/lectures/${quizLectureId}/file#page=${q.page}" target="_blank" rel="noopener">View source · Page ${q.page} ↗</a></div></div>`).join('');
    button.textContent='Submitted';inputs.forEach(x=>x.readOnly=true);
    toast('Quiz results saved to your Study progress page.');
  }catch(error){f.querySelector('.form-error').textContent=error.message;button.disabled=false;}
});

let latestSearch=0;
document.addEventListener('input',event=>{
  if(event.target.id !== 'lecture-search')return;
  const q=event.target.value.trim(),seq=++latestSearch;
  clearTimeout(lectureSearchTimer);
  if(!q){$('#lecture-search-results').innerHTML='';return;}
  lectureSearchTimer=setTimeout(async ()=>{
    try {
      const r=await api('/lectures/search?q='+encodeURIComponent(q));
      if(seq!==latestSearch||!$('#lecture-search-results'))return;
      $('#lecture-search-results').innerHTML=r.results.length ? r.results.map(x=>`<div class="saved-entry"><div><strong>${escapeHTML(x.filename)}</strong>
       <p>${escapeHTML(x.course)} · ${x.matching_pages.length ? `Text matches on page(s) ${x.matching_pages.join(', ')}` : 'Filename/course match'}</p></div>
       <a class="button secondary" href="/api/lectures/${x.id}/file${x.matching_pages.length?'#page='+x.matching_pages[0]:''}" target="_blank" rel="noopener">Open PDF ↗</a></div>`).join(''):
       '<p class="muted">No matching lecture files found.</p>';
    } catch(e){if($('#lecture-search-results'))$('#lecture-search-results').textContent=e.message;}
  },220);
});

document.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;
  try {
    if(b.dataset.notes)await openNotes(Number(b.dataset.notes));
    if(b.dataset.bookmarks)await openBookmarks(Number(b.dataset.bookmarks));
    if(b.dataset.sessionEdit){const [taskId,block]=b.dataset.sessionEdit.split(':').map(Number);editStudySession(taskId,block);}
    if(b.dataset.removeNote){
      if(!confirm('Delete this saved note?'))return;
      await api(`/notes/${Number(b.dataset.removeNote)}`,'DELETE');
      if($('#note-dialog').open)await reloadLectureNotes(); if(view==='saved')await loadSavedMaterials();toast('Note deleted.');
    }
    if(b.dataset.removeBookmark){
      if(!confirm('Delete this bookmark?'))return;
      await api(`/bookmarks/${Number(b.dataset.removeBookmark)}`,'DELETE');
      if($('#bookmark-dialog').open)await reloadBookmarks();if(view==='saved')await loadSavedMaterials();toast('Bookmark removed.');
    }
  }catch(error){toast(error.message);}
});
