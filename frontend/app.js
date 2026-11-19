"use strict";
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const icon = (id) => `<svg aria-hidden="true"><use href="#i-${id}"/></svg>`;
const freshId = () => crypto.randomUUID().replaceAll("-", "");
const state = {profiles:[], profileId:null, growth:[], plans:[], jobs:[], sessionId:freshId(), busy:false, editPlanId:null, linkedJob:null, traceSession:null};
const statusNames = {planned:"예정",in_progress:"진행 중",completed:"완료",cancelled:"취소",met:"충족",unmet:"미충족",unknown:"추가 확인",error:"오류",no_result:"조회 결과 없음",ok:"성공",failed:"실패",running:"처리 중"};
const planTypes = {application:"입사 지원",portfolio:"포트폴리오",coding_test:"코딩 테스트",cert_registration:"자격시험 접수",other:"기타 준비"};
const stages = {analyze:"요청 분석",plan:"실행 계획",execute:"도구 실행",evaluate:"제약 판단",replan:"재계획",assess_fit:"적합도 평가",respond:"답변 작성"};
const toolNames = {search_jobs:"공고 검색",get_job_detail:"공고 상세 조회",search_ncs_skills:"NCS 역량 조회",get_cert_schedule:"자격시험 일정 조회"};
let toastTimer;
function toast(message, error=false) { clearTimeout(toastTimer); $("toast").textContent=message; $("toast").className=`toast${error?" error":""}`; $("toast").hidden=false; toastTimer=setTimeout(()=>$("toast").hidden=true,6500); }
function profile() { return state.profiles.find(p=>p.id===state.profileId); }
function listValue(value) { return String(value||"").split(/[,\n]/).map(s=>s.trim()).filter(Boolean); }
function storedProfile() { try { return Number(localStorage.getItem("careermate-profile"))||null; } catch { return null; } }
function storeProfile(id) { try { localStorage.setItem("careermate-profile",id||""); } catch {} }
async function api(path, options={}) {
  let response;
  try { response=await fetch(path,{...options,headers:{"Content-Type":"application/json",...options.headers}}); }
  catch { throw new Error("서버에 연결하지 못했습니다. 서버가 실행 중인지 확인해 주세요."); }
  const data=await response.json().catch(()=>({}));
  if(!response.ok) { const error=new Error(data.error?.message||"요청을 처리하지 못했습니다."); error.data=data; error.status=response.status; throw error; }
  return data;
}
const send = (method, data) => ({method,body:JSON.stringify(data)});
function formatDate(value, withTime=false) {
  if(!value) return "미확인";
  const date=new Date(/^\d{4}-\d{2}-\d{2}$/.test(value)?`${value}T00:00:00+09:00`:value);
  if(Number.isNaN(date.getTime())) return "미확인";
  return new Intl.DateTimeFormat("ko-KR",{timeZone:"Asia/Seoul",year:"numeric",month:"2-digit",day:"2-digit",...(withTime?{hour:"2-digit",minute:"2-digit",hour12:false}:{})}).format(date);
}
function localInput(value) { return value?new Date(new Date(value).getTime()+9*3600000).toISOString().slice(0,16):""; }
function safeUrl(value) { try { const url=new URL(value); return ["http:","https:"].includes(url.protocol)?url.href:null; } catch { return null; } }
function badge(value, label) { const key=Object.hasOwn(statusNames,value)?value:"neutral"; return `<span class="badge ${key}">${esc(label||statusNames[value]||value)}</span>`; }
function navigate(view) {
  if(!["chat","plans","profile"].includes(view)) view="chat";
  document.querySelectorAll(".view").forEach(el=>el.hidden=el.id!==`${view}-view`);
  document.querySelectorAll(".nav-item").forEach(el=>{const active=el.dataset.view===view;el.classList.toggle("active",active);el.setAttribute("aria-current",active?"page":"false");});
  $("page-title").textContent={chat:"AI 취업 상담",plans:"지원 계획",profile:"내 프로필"}[view];
  history.replaceState(null,"",`#${view}`);
}
document.querySelectorAll("[data-view]").forEach(button=>button.addEventListener("click",()=>navigate(button.dataset.view)));
document.querySelector(".brand").addEventListener("click",()=>navigate("chat"));
function updateProfileUI() {
  const p=profile();
  $("profile-chip-label").textContent=p?`${p.desired_role} 프로필`:"프로필 설정";
  $("chat-profile").textContent=p?`${p.desired_role} · ${p.preferred_locations.join(", ")||"지역 무관"}`:"프로필 없이 상담 중";
  $("assess-fit").disabled=!p||state.busy;
  $("save-growth").disabled=!p;
  $("save-plan").disabled=!p;
  $("plan-profile-needed").hidden=!!p;
  const select=$("profile-select");
  select.innerHTML='<option value="">새 프로필 입력</option>'+state.profiles.map(p=>`<option value="${p.id}">${esc(p.desired_role)}${p.preferred_locations.length?` · ${esc(p.preferred_locations.join(", "))}`:""}</option>`).join("");
  select.value=p?String(p.id):"";
}
function fillProfile() {
  const form=$("profile-form"), p=profile(); form.reset();
  if(p) for(const name of ["desired_role","career_months","preferred_locations","preferred_employment_types","skills","certifications"]) form.elements[name].value=Array.isArray(p[name])?p[name].join(", "):p[name]??"";
  updateProfileUI();
}
async function loadProfiles() {
  state.profiles=(await api("/api/profiles")).profiles;
  if(!state.profiles.some(p=>p.id===state.profileId)) state.profileId=null;
  fillProfile();
}
async function chooseProfile(id) {
  if(state.busy) {$("profile-select").value=state.profileId||"";return toast("대화 처리가 끝난 뒤 프로필을 변경해 주세요.",true);}
  state.profileId=id; storeProfile(id);fillProfile();resetChat();newPlan();
  await Promise.all([loadGrowth(),loadPlans()]);
}
$("profile-select").addEventListener("change",()=>chooseProfile(Number($("profile-select").value)||null).catch(e=>toast(e.message,true)));
$("new-profile").addEventListener("click",()=>chooseProfile(null).catch(e=>toast(e.message,true)));
$("profile-form").addEventListener("submit",async event=>{
  event.preventDefault(); if(state.busy)return toast("대화 처리가 끝난 뒤 저장해 주세요.",true);
  const form=event.target, button=$("save-profile"), old=profile();
  const data={desired_role:form.elements.desired_role.value.trim(),career_months:form.elements.career_months.value===""?null:Number(form.elements.career_months.value),education_level:old?.education_level??null,education_status:old?.education_status??null,major:old?.major??null};
  for(const field of ["preferred_locations","preferred_employment_types","skills","certifications"])data[field]=listValue(form.elements[field].value);
  button.disabled=true;
  try {const p=await api(state.profileId?`/api/profiles/${state.profileId}`:"/api/profiles",send(state.profileId?"PUT":"POST",data));state.profileId=p.id;storeProfile(p.id);await loadProfiles();await Promise.all([loadGrowth(),loadPlans()]);resetChat();toast("프로필을 저장했습니다. AI 상담에서 나에게 맞는 공고를 찾아보세요.");}
  catch(error){toast(error.message,true);}finally{button.disabled=false;}
});
async function loadGrowth() {
  state.growth=state.profileId?(await api(`/api/growth-plans?profile_id=${state.profileId}`)).growth_plans:[];
  $("growth-list").innerHTML=state.growth.length?state.growth.map(g=>`<div class="growth-item"><strong>${esc(g.target_name)}</strong><p>${esc({skill:"기술 학습",certification:"자격 취득",project:"프로젝트"}[g.plan_type])} · ${formatDate(g.target_date)} 완료 목표</p>${g.expected_skills.length?`<p>기대 역량 · ${esc(g.expected_skills.join(", "))}</p>`:""}<button class="text-button danger" data-delete-growth="${g.id}">성장 계획 삭제</button></div>`).join(""):'<p class="form-hint">성장 계획을 추가하면 앞으로의 적합도를 함께 비교할 수 있어요.</p>';
  const selected=$("plan-growth").value;
  $("plan-growth").innerHTML='<option value="">연결하지 않음</option>'+state.growth.map(g=>`<option value="${g.id}">${esc(g.target_name)}</option>`).join("");
  $("plan-growth").value=selected;
}
$("growth-form").addEventListener("submit",async event=>{
  event.preventDefault();if(!state.profileId)return toast("프로필을 먼저 저장해 주세요.",true);
  const form=event.target,button=$("save-growth");button.disabled=true;
  try {await api("/api/growth-plans",send("POST",{user_profile_id:state.profileId,plan_type:form.elements.plan_type.value,target_name:form.elements.target_name.value,expected_skills:listValue(form.elements.expected_skills.value),target_date:form.elements.target_date.value,status:"planned"}));form.reset();await loadGrowth();toast("성장 계획을 추가했습니다. 다음 적합도 평가에 반영됩니다.");}
  catch(error){toast(error.message,true);}finally{button.disabled=false;}
});
async function confirmDelete(title,message) {
  const dialog=$("confirm-dialog");$("confirm-title").textContent=title;$("confirm-message").textContent=message;dialog.returnValue="";dialog.showModal();
  return new Promise(resolve=>dialog.addEventListener("close",()=>resolve(dialog.returnValue==="delete"),{once:true}));
}
$("growth-list").addEventListener("click",async event=>{
  const button=event.target.closest("[data-delete-growth]");if(!button)return;
  const id=Number(button.dataset.deleteGrowth),growth=state.growth.find(g=>g.id===id);
  if(!await confirmDelete("성장 계획을 삭제할까요?",growth?.target_name||"선택한 성장 계획"))return;
  try{await api(`/api/growth-plans/${id}?profile_id=${state.profileId}`,{method:"DELETE"});await loadGrowth();toast("성장 계획을 삭제했습니다.");}catch(error){toast(error.message,true);}
});
function scheduleFields() {
  const timed=$("schedule-kind").value==="timed",form=$("plan-form");$("timed-fields").hidden=!timed;
  form.elements.start_at.required=timed;form.elements.end_at.required=timed;form.elements.due_date.required=!timed;
  $("due-date-label").querySelector(".muted").textContent=timed?"선택":"필수";
}
$("schedule-kind").addEventListener("change",scheduleFields);
function newPlan(job=null) {
  state.editPlanId=null;state.linkedJob=job;const form=$("plan-form");form.reset();
  $("plan-warning").hidden=true;$("plan-form-title").textContent="새 지원 계획";$("save-plan").textContent="계획 저장";
  $("linked-job").hidden=!job;
  if(job) {$("linked-job").textContent=`연결 공고 · ${job.title}`;form.elements.title.value=`${job.title} 지원 준비`.slice(0,200);form.elements.plan_type.value="application";form.elements.due_date.value=job.deadline||"";$("schedule-kind").value="date";}
  else $("schedule-kind").value="timed";
  scheduleFields();
}
$("new-plan").addEventListener("click",()=>{newPlan();$("plan-form").elements.title.focus();});
async function loadPlans() {
  state.plans=state.profileId?(await api(`/api/plans?profile_id=${state.profileId}`)).plans:[];renderPlans();
}
function renderPlans() {
  $("nav-plan-count").textContent=state.plans.length;$("plan-total").textContent=state.plans.length;
  const filtered=state.plans.filter(p=>$("plan-filter").value==="all"||p.status===$("plan-filter").value);
  $("plan-list").innerHTML=filtered.length?filtered.map(p=>{
    const date=p.start_at||p.due_date;const day=date?new Intl.DateTimeFormat("ko-KR",{timeZone:"Asia/Seoul",day:"2-digit"}).format(new Date(p.start_at||`${p.due_date}T00:00:00+09:00`)).replace(/\D/g,""):"—";
    return `<article class="plan-card ${p.status==="cancelled"?"cancelled":""}"><div class="plan-date"><strong>${day}</strong><span>${esc(formatDate(date).split(".").slice(0,2).join("."))}</span></div><div class="plan-card-content">${badge(p.status)} <span class="badge neutral">${esc(planTypes[p.plan_type])}</span><h4>${esc(p.title)}</h4><p>${p.start_at?`${formatDate(p.start_at,true)} ~ ${formatDate(p.end_at,true)}`:`마감 ${formatDate(p.due_date)}`}</p>${p.start_at&&p.due_date?`<p>별도 마감일 ${formatDate(p.due_date)}</p>`:""}${p.notes?`<p>${esc(p.notes)}</p>`:""}<div class="plan-actions"><button class="text-button" data-edit-plan="${p.id}">상세·수정</button><button class="text-button danger" data-delete-plan="${p.id}">삭제</button></div></div></article>`;
  }).join(""):`<div class="empty-state">${icon("calendar")}<h3>${state.profileId?"아직 등록한 계획이 없어요":"프로필을 먼저 입력해 주세요"}</h3><p>${state.profileId?"추천 공고에서 준비 계획을 만들거나<br>새 일정을 직접 등록해 보세요.":"나의 프로필을 저장하면<br>지원 일정을 등록할 수 있어요."}</p></div>`;
}
$("plan-filter").addEventListener("change",renderPlans);
$("plan-list").addEventListener("click",async event=>{
  const edit=event.target.closest("[data-edit-plan]"),del=event.target.closest("[data-delete-plan]");
  try {
    if(edit) {const formElement=$("plan-form");formElement.inert=true;$("plan-form-title").textContent="계획 불러오는 중";try {const p=await api(`/api/plans/${edit.dataset.editPlan}?profile_id=${state.profileId}`);newPlan();state.editPlanId=p.id;state.linkedJob=p.job_posting_id?{id:p.job_posting_id,title:p.title}:null;$("plan-form-title").textContent="지원 계획 수정";$("save-plan").textContent="변경 저장";const form=$("plan-form");for(const key of ["title","plan_type","status","due_date","notes","growth_plan_id"])form.elements[key].value=p[key]??"";form.elements.start_at.value=localInput(p.start_at);form.elements.end_at.value=localInput(p.end_at);$("schedule-kind").value=p.start_at?"timed":"date";scheduleFields();$("plan-form-title").scrollIntoView({behavior:"smooth",block:"center"});}finally{formElement.inert=false;}}
    if(del) {const p=state.plans.find(p=>p.id===Number(del.dataset.deletePlan));if(!await confirmDelete("지원 계획을 삭제할까요?",p?.title||"선택한 지원 계획"))return;await api(`/api/plans/${del.dataset.deletePlan}?profile_id=${state.profileId}`,{method:"DELETE"});if(state.editPlanId===Number(del.dataset.deletePlan))newPlan();await loadPlans();toast("지원 계획을 삭제했습니다.");}
  } catch(error){toast(error.message,true);}
});
$("plan-form").addEventListener("submit",async event=>{
  event.preventDefault();if(!state.profileId)return toast("프로필을 먼저 저장해 주세요.",true);
  const form=event.target,timed=$("schedule-kind").value==="timed",button=$("save-plan");
  const data={job_posting_id:state.linkedJob?.id||null,growth_plan_id:Number(form.elements.growth_plan_id.value)||null,title:form.elements.title.value,plan_type:form.elements.plan_type.value,status:form.elements.status.value,due_date:form.elements.due_date.value||null,start_at:timed?`${form.elements.start_at.value}:00+09:00`:null,end_at:timed?`${form.elements.end_at.value}:00+09:00`:null,notes:form.elements.notes.value||null};
  if(timed&&new Date(data.end_at)<=new Date(data.start_at))return toast("종료 일시는 시작 일시보다 늦어야 합니다.",true);
  button.disabled=true;
  try {
    const editing=state.editPlanId;
    const result=await api(editing?`/api/plans/${editing}?profile_id=${state.profileId}`:"/api/plans",send(editing?"PATCH":"POST",editing?data:{...data,user_profile_id:state.profileId}));
    state.editPlanId=result.plan.id;$("plan-form-title").textContent="지원 계획 수정";button.textContent="변경 저장";
    $("plan-warning").hidden=!result.warning;
    if(result.warning)$("plan-warning").innerHTML=`<strong>${esc(result.warning)}</strong><ul>${result.conflicts.map(p=>`<li>${esc(p.title)} · ${formatDate(p.start_at,true)} ~ ${formatDate(p.end_at,true)}</li>`).join("")}</ul>`;
    await loadPlans();toast(result.warning||"지원 계획을 저장했습니다.");
  } catch(error){toast(error.message,true);}finally{button.disabled=!state.profileId;}
});
function addMessage(role,text,error=false) {
  $("welcome")?.remove();const message=document.createElement("div");message.className=`message ${role}${error?" error":""}`;
  const label=document.createElement("span");label.className="message-label";label.textContent=role==="user"?"나":"CareerMate";
  const bubble=document.createElement("div");bubble.className="bubble";bubble.textContent=text;message.append(label,bubble);$("messages").append(message);$("messages").scrollTop=$("messages").scrollHeight;
}
const welcomeHTML=$("messages").innerHTML;
function resetChat() {
  if(state.busy)return;
  state.sessionId=freshId();state.traceSession=null;state.jobs=[];$("messages").innerHTML=welcomeHTML;$("job-results").innerHTML="";$("log-session").value="";$("message-input").value="";renderTrace([]);setRunStatus("neutral","대기");
}
$("new-chat").addEventListener("click",()=>{resetChat();toast("새 대화를 시작합니다.");});
function setRunStatus(kind,label){$("run-status").className=`badge ${kind}`;$("run-status").textContent=label;}
function setBusy(busy) {
  state.busy=busy;$("chat-loading").hidden=!busy;$("send-message").disabled=busy;$("new-chat").disabled=busy;$("message-input").disabled=busy;$("assess-fit").disabled=busy||!profile();$("delete-logs").disabled=busy||!state.traceSession;
}
$("messages").addEventListener("click",event=>{const prompt=event.target.closest("[data-prompt]");if(prompt){$("message-input").value=prompt.dataset.prompt;$("chat-form").requestSubmit();}});
$("message-input").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing){event.preventDefault();$("chat-form").requestSubmit();}});
$("chat-form").addEventListener("submit",async event=>{
  event.preventDefault();const text=$("message-input").value.trim();if(!text||state.busy)return;
  addMessage("user",text);$("message-input").value="";setBusy(true);setRunStatus("running","처리 중");
  try {
    const result=await api("/api/chat",send("POST",{message:text,session_id:state.sessionId,profile_id:state.profileId,assess_fit:!!profile()&&$("assess-fit").checked}));
    addMessage("assistant",result.answer);renderJobs(result);$("log-session").value="";state.traceSession=result.session_id;renderTrace(result.events);setRunStatus("completed","완료");
    const missing=result.tool_calls.find(call=>call.error_code==="MISSING_API_KEY");if(missing)addMessage("assistant",`${toolNames[missing.tool]}에 필요한 API 키가 없습니다. .env의 ${["search_jobs","get_job_detail"].includes(missing.tool)?"ALIO_API_KEY":"DATA_GO_KR_KEY"} 설정을 확인해 주세요.`,true);
    if(result.used_previous_context)toast("이전 조건을 유지하고 변경한 조건을 반영했습니다.");
    await refreshLogs();
  } catch(error){addMessage("assistant",error.message,true);renderTrace(error.data?.events||[]);setRunStatus("failed","실패");toast(error.message,true);}
  finally {setBusy(false);$("message-input").focus();}
});
function renderTrace(events) {
  if(!events.length){state.traceSession=null;$("delete-logs").disabled=true;$("trace").innerHTML=`<div class="trace-empty">${icon("log")}<strong>추천의 과정을 확인하세요</strong><p>실행 단계와 도구 호출,<br>재계획 이유를 순서대로 보여드려요.</p></div>`;return;}
  let lastRun=null;
  $("trace").innerHTML=events.map(event=>{
    const p=event.payload||{},s=stages[p.stage]||p.stage||"",tool=toolNames[p.tool]||p.tool||"";
    const labels={run_started:"요청 시작",run_finished:"요청 종료",stage_started:`${s} 진입`,stage_finished:`${s} 종료`,intent_decided:"의도 판단",plan_created:"실행 순서 구성",constraint_decided:"조건 충족 판단",route_decided:`${stages[p.next_stage]||"다음 단계"} 선택`,replan_requested:`재계획 ${p.replan_count||""}회`,tool_requested:`${tool} 요청`,tool_finished:`${tool} 응답`,fit_decided:"현재·미래 적합도 판단",error:"처리 오류"};
    const header=event.run_id!==lastRun?`<div class="trace-run">실행 ${esc(event.run_id.slice(0,8))}</div>`:"";lastRun=event.run_id;
    const reason=p.reason||(["tool_finished","constraint_decided"].includes(event.event_type)?[p.status?statusNames[p.status]:null,p.count!=null?`${p.count}건`:null,p.deadline_status==="not_requested"?"마감 조건 없음":p.deadline_status==="unmet"?"마감 조건 미충족":p.deadline_status==="unknown"?"마감일 확인 필요":null].filter(Boolean).join(" · "):"");
    const type=event.event_type==="replan_requested"?"replan":event.event_type==="error"||p.status==="error"?"error":"";
    return `${header}<div class="trace-item ${type}">${event.duration_ms!=null?`<span class="duration">${event.duration_ms}ms</span>`:""}<strong>${esc(labels[event.event_type]||event.event_type)}</strong>${reason?`<small>${esc(reason)}</small>`:""}${p.error_code?`<small>${esc(p.error_code==="MISSING_API_KEY"?"API 키 설정 필요":p.error_code)}</small>`:""}${p.data_source?`<small>${p.data_source==="external_api"?"외부 API 조회":"확보한 후보 재사용"}</small>`:""}</div>`;
  }).join("");
  const end=events.filter(e=>e.event_type==="run_finished").at(-1);if(end)setRunStatus(end.payload?.status==="failed"?"failed":"completed",end.payload?.status==="failed"?"실패":"완료");
}
async function refreshLogs() {
  const current=$("log-session").value;const data=await api("/api/logs?limit=100");
  $("log-session").innerHTML='<option value="">현재 대화</option>'+data.sessions.map(s=>`<option value="${esc(s.session_id)}">${esc(s.session_id.slice(0,20))} · 실행 ${s.run_count}회</option>`).join("");
  $("log-session").value=current;
}
async function loadLog() {
  const id=$("log-session").value||state.sessionId;
  try {let offset=0,events=[],total=0;do{const data=await api(`/api/logs/${encodeURIComponent(id)}?limit=1000&offset=${offset}`);events.push(...data.events);total=data.total;offset=events.length;}while(offset<total);renderTrace(events);state.traceSession=id;$("delete-logs").disabled=state.busy;}
  catch(error){if(error.status===404){renderTrace([]);setRunStatus("neutral","대기");}else toast(error.message,true);}
}
$("log-session").addEventListener("change",loadLog);
$("refresh-logs").addEventListener("click",()=>refreshLogs().then(loadLog).catch(e=>toast(e.message,true)));
$("delete-logs").addEventListener("click",async()=>{
  if(!state.traceSession||state.busy)return;
  if(!await confirmDelete("실행 기록을 삭제할까요?","선택한 대화의 실행 기록을 모두 삭제합니다. 지원 계획은 유지됩니다."))return;
  try{await api(`/api/logs/${encodeURIComponent(state.traceSession)}`,{method:"DELETE"});$("log-session").value="";renderTrace([]);setRunStatus("neutral","대기");await refreshLogs();toast("실행 기록을 삭제했습니다.");}catch(error){toast(error.message,true);}
});
function scoreBlock(fit,label,future=false) {const weight=fit.assessed_weight||0;return `<div class="score-block ${future?"future":""}"><div class="score-label">${label}</div><div class="score-value">${fit.score}<small> / ${weight}점</small></div><div class="score-track"><i style="width:${weight?Math.min(100,Math.max(0,fit.score/weight*100)):0}%"></i></div></div>`;}
function renderJobs(result) {
  if(!result.analysis.intents.some(i=>["search_jobs","get_job_detail"].includes(i)))return;
  state.jobs=result.jobs;
  if(!result.jobs.length){$("job-results").innerHTML=`<div class="notice">${result.search_summary?.search_status==="no_result"?"검색 결과 0건입니다. 조건을 변경해 다시 검색해 보세요.":result.search_summary?.search_status==="error"?"공고 검색 오류로 결과를 확인하지 못했습니다. 실행 기록을 확인해 주세요.":"요청한 조건을 충족하는 공고가 없습니다."}</div>`;}
  else $("job-results").innerHTML=`<div class="results-heading"><h3>나에게 온 기회 <span class="count">${result.jobs.length}</span></h3><span class="results-note">점수는 확인된 배점 기준의 참고 값입니다</span></div><div class="job-grid">${result.jobs.map(job=>{
    const match=result.matches.find(m=>m.job_id===job.id),url=safeUrl(job.job_url),meta=[...(job.location||[]),...(job.employment_type||[]),job.deadline?`마감 ${formatDate(job.deadline)}`:"마감일 미확인"];
    const scored=match?.current&&match?.future;
    return `<article class="job-card"><div class="job-company"><span>${esc(job.company||"채용 기관")}</span><span class="badge neutral">${job.source==="alio"?"잡알리오":"외부 채용공고"}</span></div><h4>${esc(job.title)}</h4><div class="job-meta">${meta.map(s=>`<span>${esc(s)}</span>`).join("")}</div>${scored?`<div class="scores">${scoreBlock(match.current,"현재 적합도")}${scoreBlock(match.future,"미래 예상 적합도",true)}</div><div class="eligibility-line">현재 필수조건 ${badge(match.current.eligibility)}</div><div class="eligibility-line">미래 필수조건 ${badge(match.future.eligibility)}</div><details><summary>판단 근거와 확인할 내용</summary><p>확인 배점: 현재 ${match.current.assessed_weight}점 · 미래 ${match.future.assessed_weight}점</p><p>프로필에서 확인되지 않은 역량: ${esc(match.current.gaps?.join(", ")||"없음")}</p><p>추가 확인: ${esc([...(match.current.unknown||[]),...(match.current.blockers||[])].join(", ")||"없음")}</p><p>반영한 성장 계획: ${esc(match.applied_plans?.join(", ")||"없음")}</p>${match.other_preferred?.length?`<p>기타 우대조건: ${esc(match.other_preferred.join(", "))} (기술 점수와 별도)</p>`:""}<p>점수 세부 기준: ${esc(Object.entries(match.current.by_criterion||{}).map(([k,v])=>`${k} ${v??"미확인"}`).join(" · "))}</p></details>`:`<p class="reference-note">${match?.error?"이 공고의 적합도를 계산하지 못했습니다.":profile()?"적합도 계산을 켜고 다시 요청하면 점수를 비교할 수 있어요.":"프로필을 저장하면 현재·미래 적합도를 비교할 수 있어요."}</p>`}<div class="job-actions">${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">원문 공고 보기</a>`:'<span class="form-hint">원문 링크 미확인</span>'}<button class="button secondary" data-job-plan="${job.id}">${icon("calendar")}준비 계획 만들기</button></div></article>`;
  }).join("")}</div><p class="reference-note">현재 점수와 미래 예상 점수는 규칙 기반 참고 값이며 합격 확률이 아닙니다. 필수조건이 ‘추가 확인’인 경우 공고 원문을 확인해 주세요.</p>`;
  if(result.excluded_jobs.length)$("job-results").insertAdjacentHTML("beforeend",`<details class="excluded-list"><summary>추천에서 제외한 공고 ${result.excluded_jobs.length}건</summary><ul>${result.excluded_jobs.map(job=>`<li>${esc(job.title)} · ${job.deadline_status==="unmet"?"요청한 마감 조건 미충족":"마감일 미확인으로 조건을 판단하지 못함"}</li>`).join("")}</ul></details>`);
}
$("job-results").addEventListener("click",event=>{const button=event.target.closest("[data-job-plan]");if(!button)return;if(!profile()){toast("준비 계획을 저장하려면 프로필을 먼저 입력해 주세요.",true);navigate("profile");return;}const job=state.jobs.find(j=>j.id===Number(button.dataset.jobPlan));newPlan(job);navigate("plans");$("plan-form").elements.title.focus();});
async function init() {
  state.profileId=storedProfile();newPlan();navigate(location.hash.slice(1)||"chat");
  try {await api("/health");$("server-status").textContent="서버 연결됨";$("server-dot").classList.add("connected");await loadProfiles();await Promise.all([loadGrowth(),loadPlans(),refreshLogs()]);}
  catch(error){$("server-status").textContent="연결 확인 필요";toast(error.message,true);}
}
window.addEventListener("resize",()=>requestAnimationFrame(()=>{$("messages").scrollTop=$("messages").scrollHeight;}));
init();
