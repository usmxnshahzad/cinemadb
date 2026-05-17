/* =========================================================
   IMSR-DB  |  Main JavaScript
   Mood Finder + Chatbot + Interactions
   ========================================================= */

// ─── Toast ─────────────────────────────────────────────────
function showToast(msg, type = 'info', duration = 3200) {
  const area = document.getElementById('toast-area');
  if (!area) return;
  const icons = { ok: '&#10003;', err: '&#10007;', info: '&#9432;' };
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.innerHTML = `<span class="toast-icon">${icons[type]||icons.info}</span>${msg}`;
  area.appendChild(t);
  setTimeout(() => {
    t.style.transition = '.3s'; t.style.opacity = '0'; t.style.transform = 'translateX(30px)';
    setTimeout(() => t.remove(), 330);
  }, duration);
}
window.showToast = showToast;

// ─── Navbar scroll ──────────────────────────────────────────
const navbar = document.getElementById('navbar');
if (navbar) {
  const onScroll = () => navbar.classList.toggle('scrolled', window.scrollY > 20);
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
}

// ─── Poster error fallback ──────────────────────────────────
document.querySelectorAll('img').forEach(img =>
  img.addEventListener('error', () => { img.src = '/static/images/placeholder.svg'; })
);

// ─── Horizontal row wheel scroll ────────────────────────────
document.querySelectorAll('.movie-row').forEach(row => {
  row.addEventListener('wheel', e => {
    if (Math.abs(e.deltaX) < Math.abs(e.deltaY)) {
      e.preventDefault();
      row.scrollLeft += e.deltaY * 1.2;
    }
  }, { passive: false });
});

// ─── Favourite toggle ────────────────────────────────────────
document.addEventListener('click', async e => {
  const btn = e.target.closest('[data-fav]');
  if (!btn) return;
  e.preventDefault(); e.stopPropagation();
  const mid = parseInt(btn.dataset.fav);
  const res = await fetch('/api/favorite', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ movie_id: mid })
  });
  if (res.status === 401) { showToast('Please log in first', 'err'); return; }
  const d = await res.json();
  const added = d.status === 'added';
  document.querySelectorAll(`[data-fav="${mid}"]`).forEach(b => {
    b.classList.toggle('fav-on', added);
    const icon = b.querySelector('.fav-icon') || b;
    if (b.querySelector('.fav-icon')) b.querySelector('.fav-icon').innerHTML = added ? '&#9829;' : '&#9825;';
  });
  showToast(added ? 'Added to favourites' : 'Removed from favourites', added ? 'ok' : 'info');
});

// ─── Watchlist toggle ───────────────────────────────────────
document.addEventListener('click', async e => {
  const btn = e.target.closest('[data-wl]');
  if (!btn) return;
  e.preventDefault(); e.stopPropagation();
  const mid = parseInt(btn.dataset.wl);
  const res = await fetch('/api/watchlist', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ movie_id: mid })
  });
  if (res.status === 401) { showToast('Please log in first', 'err'); return; }
  const d = await res.json();
  const added = d.status === 'added';
  document.querySelectorAll(`[data-wl="${mid}"]`).forEach(b => {
    b.classList.toggle('wl-on', added);
  });
  showToast(added ? 'Added to watchlist' : 'Removed from watchlist', added ? 'ok' : 'info');
});

// ─────────────────────────────────────────────────────────────
// MOOD FINDER
// ─────────────────────────────────────────────────────────────
const MOOD_Q = {
  questions: [
    {
      id: 'current_feeling',
      text: 'How are you feeling right now?',
      sub: 'Pick what resonates most',
      options: [
        { value:'happy',    label:'Happy',       icon:'&#9733;', desc:'Light-hearted and good' },
        { value:'sad',      label:'Melancholic',  icon:'&#126;',  desc:'Need something emotional' },
        { value:'excited',  label:'Excited',      icon:'&#33;',   desc:'Ready for action' },
        { value:'anxious',  label:'Tense',        icon:'&#35;',   desc:'Edge of my seat feeling' },
        { value:'calm',     label:'Peaceful',     icon:'&#9675;', desc:'Relaxed and reflective' },
        { value:'bored',    label:'Bored',        icon:'&#8722;', desc:'Need something fresh' },
      ]
    },
    {
      id: 'company',
      text: 'Who are you watching with?',
      sub: 'This shapes the vibe we pick',
      options: [
        { value:'alone',    label:'Solo',        icon:'1', desc:'Just me tonight' },
        { value:'partner',  label:'Date Night',  icon:'2', desc:'Someone special' },
        { value:'friends',  label:'Friend Group',icon:'3', desc:'Social viewing' },
        { value:'family',   label:'Family',      icon:'4', desc:'All ages welcome' },
      ]
    },
    {
      id: 'intensity',
      text: 'What intensity level suits you?',
      sub: 'Emotional weight of the story',
      options: [
        { value:'light',    label:'Light & Fun', icon:'L', desc:'Easy, feel-good' },
        { value:'medium',   label:'Balanced',    icon:'M', desc:'Engaging but not heavy' },
        { value:'heavy',    label:'Deep & Dark', icon:'H', desc:'Complex, thought-provoking' },
        { value:'intense',  label:'Intense',     icon:'I', desc:'Heart-pounding, gripping' },
      ]
    },
    {
      id: 'length_pref',
      text: 'How much time do you have?',
      sub: 'We match the runtime accordingly',
      options: [
        { value:'short',    label:'Under 90 min', icon:'&lt;', desc:'Quick watch' },
        { value:'medium',   label:'90&ndash;120',  icon:'=',   desc:'Standard film' },
        { value:'long',     label:'Epic 2h+',      icon:'&gt;', desc:'Full experience' },
        { value:'series',   label:'TV Series',     icon:'S',   desc:'Multi-episode binge' },
      ]
    }
  ]
};

let moodStep = 0;
let moodAnswers = {};
const totalSteps = MOOD_Q.questions.length;

function renderMoodProgress() {
  return MOOD_Q.questions.map((_, i) =>
    `<div class="mp-dot ${i < moodStep ? 'done' : i === moodStep ? 'active' : ''}"></div>`
  ).join('');
}

function renderMoodStep(idx) {
  const q = MOOD_Q.questions[idx];
  const cur = moodAnswers[q.id] || null;
  return `
    <div class="mood-progress">${renderMoodProgress()}</div>
    <div class="mood-kicker">Step ${idx+1} of ${totalSteps}</div>
    <h2 class="mood-headline">${q.text}</h2>
    <p class="mood-sub">${q.sub}</p>
    <div class="mood-grid" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr));">
      ${q.options.map(opt => `
        <div class="mood-card ${cur===opt.value?'selected':''}" data-val="${opt.value}" data-qid="${q.id}">
          <span class="mood-icon">${opt.icon}</span>
          <div class="mood-label">${opt.label}</div>
          <div class="mood-desc">${opt.desc}</div>
        </div>`).join('')}
    </div>
    <div class="mood-nav">
      ${idx > 0 ? `<button class="btn-mood-back" id="mood-back">Back</button>` : ''}
      <button class="btn-mood-next" id="mood-next">${idx < totalSteps-1 ? 'Next' : 'Show My Films'}</button>
    </div>`;
}

function renderMoodResults(movies, genreScores) {
  const topGenres = Object.entries(genreScores)
    .sort((a,b) => b[1]-a[1]).slice(0,3).map(([g]) => g).join(', ');
  return `
    <div class="mood-progress">${MOOD_Q.questions.map(() => '<div class="mp-dot done"></div>').join('')}</div>
    <div class="mood-kicker">Your Mood Profile</div>
    <h2 class="mood-headline">Films Matched for You</h2>
    <p class="mood-sub">Best genres: <strong>${topGenres}</strong></p>
    <div class="mood-results-grid" id="mood-res-grid">
      ${movies.map(m => `
        <a href="/movie/${m.id}" style="text-decoration:none;">
          <div class="movie-card">
            <img class="movie-card-img" src="${m.poster}" alt="${m.title}" loading="lazy" onerror="this.src='/static/images/placeholder.svg'">
            <div class="movie-card-body">
              <div class="mc-title">${m.title}</div>
              <div class="mc-row"><span class="mc-rating">&#9733; ${m.rating}</span><span>${m.year||''}</span></div>
            </div>
          </div>
        </a>`).join('')}
    </div>
    <div class="mood-nav" style="margin-top:1.5rem;">
      <button class="btn-mood-back" id="mood-restart">Try Again</button>
      <button class="btn-mood-next" onclick="closeMood()">Done</button>
    </div>`;
}

function attachMoodHandlers() {
  const box = document.getElementById('mood-box');
  if (!box) return;

  // Card selection
  box.querySelectorAll('.mood-card').forEach(card => {
    card.addEventListener('click', () => {
      const qid = card.dataset.qid;
      box.querySelectorAll(`.mood-card`).forEach(c => c.classList.remove('selected'));
      card.classList.add('selected');
      moodAnswers[qid] = card.dataset.val;
    });
  });

  // Next
  document.getElementById('mood-next')?.addEventListener('click', async () => {
    const q = MOOD_Q.questions[moodStep];
    if (!moodAnswers[q.id]) { showToast('Please pick an option', 'err'); return; }
    if (moodStep < totalSteps - 1) {
      moodStep++;
      box.innerHTML = renderMoodStep(moodStep);
      attachMoodHandlers();
    } else {
      // Fetch results
      box.innerHTML = `<div class="mood-kicker">Analysing your mood&hellip;</div><div class="loading-pulse"><div class="lp-bar"></div><div class="lp-bar"></div><div class="lp-bar"></div><div class="lp-bar"></div></div>`;
      try {
        const res  = await fetch('/api/mood/recommend', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ answers: moodAnswers })
        });
        const data = await res.json();
        box.innerHTML = renderMoodResults(data.movies || [], data.genre_scores || {});
        attachMoodHandlers();
      } catch {
        box.innerHTML = `<p style="color:var(--muted)">Something went wrong. Please try again.</p><button class="btn-mood-next" onclick="closeMood()">Close</button>`;
      }
    }
  });

  // Back
  document.getElementById('mood-back')?.addEventListener('click', () => {
    if (moodStep > 0) { moodStep--; box.innerHTML = renderMoodStep(moodStep); attachMoodHandlers(); }
  });

  // Restart
  document.getElementById('mood-restart')?.addEventListener('click', () => {
    moodStep = 0; moodAnswers = {};
    box.innerHTML = renderMoodStep(0);
    attachMoodHandlers();
  });
}

function openMood() {
  moodStep = 0; moodAnswers = {};
  const box = document.getElementById('mood-box');
  if (box) { box.innerHTML = renderMoodStep(0); attachMoodHandlers(); }
  document.getElementById('mood-overlay')?.classList.add('open');
  document.body.style.overflow = 'hidden';
}
function closeMood() {
  document.getElementById('mood-overlay')?.classList.remove('open');
  document.body.style.overflow = '';
}
window.closeMood = closeMood;

document.getElementById('open-mood')?.addEventListener('click', openMood);
document.getElementById('close-mood')?.addEventListener('click', closeMood);
document.getElementById('mood-overlay')?.addEventListener('click', e => {
  if (e.target === document.getElementById('mood-overlay')) closeMood();
});

// ─────────────────────────────────────────────────────────────
// CHATBOT
// ─────────────────────────────────────────────────────────────
const chatWindow = document.getElementById('chatbot-window');
const chatFab    = document.getElementById('chatbot-fab');
const chatInput  = document.getElementById('chat-input');
const chatMsgs   = document.getElementById('chat-messages');

function openChat()  { chatWindow?.classList.add('open'); chatFab?.querySelector('.fab-badge')?.remove(); }
function closeChat() { chatWindow?.classList.remove('open'); }

chatFab?.addEventListener('click', () => chatWindow?.classList.contains('open') ? closeChat() : openChat());
document.getElementById('close-chat')?.addEventListener('click', closeChat);
document.getElementById('toggle-chat')?.addEventListener('click', () =>
  chatWindow?.classList.contains('open') ? closeChat() : openChat()
);

function appendMsg(text, role) {
  const now  = new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
  const div  = document.createElement('div');
  div.className = `chat-msg ${role}`;
  div.innerHTML = `<div class="chat-bubble">${text}</div><div class="chat-time">${now}</div>`;
  chatMsgs?.appendChild(div);
  chatMsgs.scrollTop = chatMsgs.scrollHeight;
}

function showTyping() {
  const div = document.createElement('div');
  div.className = 'chat-msg bot'; div.id = 'typing-ind';
  div.innerHTML = `<div class="chat-bubble"><div class="chat-typing"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div>`;
  chatMsgs?.appendChild(div);
  chatMsgs.scrollTop = chatMsgs.scrollHeight;
}
function removeTyping() { document.getElementById('typing-ind')?.remove(); }

async function sendMessage(text) {
  if (!text.trim()) return;
  appendMsg(text, 'user');
  if (chatInput) chatInput.value = '';
  showTyping();
  try {
    const res  = await fetch('/api/chat', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ message: text })
    });
    const data = await res.json();
    removeTyping();
    appendMsg(data.reply || 'Sorry, I could not respond.', 'bot');
  } catch {
    removeTyping();
    appendMsg('Network error. Please try again.', 'bot');
  }
}

document.getElementById('chat-send')?.addEventListener('click', () => sendMessage(chatInput?.value || ''));
chatInput?.addEventListener('keydown', e => { if (e.key === 'Enter') sendMessage(chatInput.value); });
document.querySelectorAll('.chat-chip').forEach(chip =>
  chip.addEventListener('click', () => sendMessage(chip.dataset.msg || chip.textContent))
);

// Keyboard escape
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeMood(); closeChat(); }
});
