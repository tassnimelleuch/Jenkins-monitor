
// ── Chatbot
let chatOpen=false;
function toggleChat(){
  chatOpen=!chatOpen;
  document.getElementById('chatPanel').classList.toggle('open',chatOpen);
  if(chatOpen)setTimeout(()=>document.getElementById('chatInput').focus(),320);
}
function resize(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,78)+'px';}
function onKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}}
function useSugg(el){document.getElementById('chatInput').value=el.textContent;document.getElementById('chatSugg').style.display='none';send();}
function nowStr(){return new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});}
function addMsg(txt,role){
  const box=document.getElementById('chatMsgs');
  const d=document.createElement('div');d.className='msg '+role;
  d.innerHTML=`<div class="bubble">${txt}</div><div class="msg-time">${nowStr()}</div>`;
  box.appendChild(d);box.scrollTop=box.scrollHeight;
}
function showTyping(){const box=document.getElementById('chatMsgs');const t=document.createElement('div');t.className='typing-bbl';t.id='typing';t.innerHTML='<span></span><span></span><span></span>';box.appendChild(t);box.scrollTop=box.scrollHeight;}
function hideTyping(){const t=document.getElementById('typing');if(t)t.remove();}
const BOT=[
  {p:/fail|error/i,      r:"The last failure was likely due to a test timeout or environment issue. Check the build console for details."},
  {p:/coverage|trend/i,  r:"Coverage has been trending upward. Check the Pipeline KPIs section for the full breakdown."},
  {p:/slow|duration/i,   r:"The slowest builds are usually the ones running full integration test suites."},
  {p:/deploy|status/i,   r:"Last deployment completed successfully. All health checks passed."},
  {p:/hello|hi|hey/i,    r:"Hey! What would you like to know about your pipeline?"},
  {p:/success|rate/i,    r:"Your current success rate is shown in the KPI circles on the dashboard."},
  {p:/health/i,          r:"Health score reflects the ratio of successful builds over the last 10 runs."},
];
function getBotReply(m){for(const b of BOT)if(b.p.test(m))return b.r;return "I don't have specific data on that yet. Try asking about failures, coverage, or deployments.";}
async function send(){
  const inp=document.getElementById('chatInput');
  const txt=inp.value.trim();if(!txt)return;
  addMsg(txt,'user');inp.value='';inp.style.height='auto';
  document.getElementById('chatSugg').style.display='none';
  showTyping();
  await new Promise(r=>setTimeout(r,800+Math.random()*600));
  hideTyping();addMsg(getBotReply(txt),'bot');
}

// ── Toast
function showToast(msg,cls=''){
  const t=document.getElementById('toast');
  if (!t) return;
  t.textContent=msg;t.className='toast '+(cls||'');t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),3000);
}

// ── PDF Export
function exportPDF(){
  const {jsPDF}=window.jspdf;
  const doc=new jsPDF({orientation:'landscape',unit:'mm',format:'a4'});
  const dark=document.documentElement.getAttribute('data-theme')==='dark';
  const ts=new Date().toLocaleString();
  doc.setFillColor(dark?11:240,dark?11:240,dark?18:248);doc.rect(0,0,297,210,'F');
  doc.setFillColor(124,111,255);doc.rect(0,0,297,22,'F');
  doc.setTextColor(255,255,255);doc.setFontSize(14);doc.setFont('helvetica','bold');
  doc.text('Jenkins Monitor — KPI Report',14,13);
  doc.setFontSize(8);doc.setFont('helvetica','normal');
  doc.text(`Generated: ${ts}  |  Pipeline: django-pipeline  |  Branch: main`,14,20);
  const total=document.getElementById('sv-total').textContent;
  const succ=document.getElementById('sv-success').textContent;
  const fail=document.getElementById('sv-failed').textContent;
  const abrt=document.getElementById('sv-aborted').textContent;
  const health=document.getElementById('health-val').textContent+'%';
  const rate=document.getElementById('rate-val').textContent+'%';
  const kpis=[
    {l:'Total Builds',v:total,s:'All time',c:[124,111,255]},
    {l:'Successful',v:succ,s:'Last 30 days',c:[0,219,160]},
    {l:'Failed',v:fail,s:'Last 30 days',c:[255,69,96]},
    {l:'Aborted',v:abrt,s:'Last 30 days',c:[255,140,66]},
    {l:'Health Score',v:health,s:'Index',c:[0,219,160]},
    {l:'Success Rate',v:rate,s:'Last 30 days',c:[58,184,248]},
  ];
  doc.setTextColor(dark?190:40,dark?190:40,dark?210:60);
  doc.setFontSize(9);doc.setFont('helvetica','bold');
  doc.text('KEY PERFORMANCE INDICATORS',14,32);
  kpis.forEach((k,i)=>{
    const x=14+i*47,y=36,w=44,h=30,[r,g,b]=k.c;
    doc.setFillColor(dark?18:255,dark?18:255,dark?28:255);doc.roundedRect(x,y,w,h,3,3,'F');
    doc.setFillColor(r,g,b);doc.roundedRect(x,y,w,3,1,1,'F');
    doc.setTextColor(dark?120:100,dark?120:100,dark?150:130);
    doc.setFontSize(6.5);doc.setFont('helvetica','bold');
    doc.text(k.l.toUpperCase(),x+3,y+9);
    doc.setTextColor(r,g,b);doc.setFontSize(13);doc.setFont('helvetica','bold');
    doc.text(k.v,x+3,y+20);
    doc.setTextColor(dark?120:100,dark?120:100,dark?150:130);
    doc.setFontSize(6.5);doc.setFont('helvetica','normal');
    doc.text(k.s,x+3,y+27);
  });
  doc.save(`jenkins-report-${Date.now()}.pdf`);
  showToast('PDF exported successfully');
}