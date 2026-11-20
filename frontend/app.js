"use strict";
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const icon = (id) => `<svg aria-hidden="true"><use href="#i-${id}"/></svg>`;
const freshId = () => crypto.randomUUID().replaceAll("-", "");
const state = {drafts:[], pendingDraft:null, profiles:[], profileId:null, growth:[], plans:[], jobs:[], sessionId:freshId(), busy:false, editPlanId:null, linkedJob:null, traceSession:null};
const statusNames = {planned:"예정",in_progress:"진행 중",completed:"완료",cancelled:"취소",met:"충족",unmet:"미충족",unknown:"추가 확인",error:"오류",no_result:"조회 결과 없음",ok:"성공",failed:"실패",running:"처리 중"};
const planTypes = {application:"입사 지원",portfolio:"포트폴리오",coding_test:"코딩 테스트",cert_registration:"자격시험 접수",other:"기타 준비"};
const stages = {analyze:"요청 분석",plan:"실행 계획",execute:"도구 실행",evaluate:"제약 판단",replan:"재계획",assess_fit:"적합도 평가",respond:"답변 작성"};
const toolNames = {search_jobs:"공고 검색",get_job_detail:"공고 상세 조회",search_ncs_skills:"NCS 역량 조회",get_cert_schedule:"자격시험 일정 조회"};
const sourceName = (source) => ({alio:"잡알리오",incruit:"인크루트 RSS",greenhouse:"Greenhouse",lever:"Lever"}[source]||"채용 출처");
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
  $("profile-chip-label").textContent=p?`${p.profile_name||p.desired_role} · #${p.id}`:"프로필 설정";
  $("chat-profile").textContent=p?`${p.desired_role} · ${p.preferred_locations.join(", ")||"지역 무관"}`:"프로필 없이 상담 중";
  $("assess-fit").disabled=!p||state.busy||$("job-source").value==="incruit";
  $("save-growth").disabled=!p;
  $("save-plan").disabled=!p;
  $("plan-profile-needed").hidden=!!p;
  const select=$("profile-select");
  select.innerHTML='<option value="">새 프로필 입력</option>'+state.profiles.map(p=>`<option value="${p.id}">${esc(p.profile_name||p.desired_role)} · #${p.id}${p.preferred_locations.length?` · ${esc(p.preferred_locations.join(", "))}`:""}</option>`).join("");
  select.value=p?String(p.id):"";
  $("active-profile-select").innerHTML='<option value="">프로필 없이 상담</option>'+state.profiles.map(item=>`<option value="${item.id}">${esc(item.profile_name||item.desired_role)} · #${item.id}</option>`).join("");
  $("active-profile-select").value=p?String(p.id):"";
  $("active-profile-select").disabled=state.busy;
  updateExperienceOwner();
}
function fillProfile() {
  const form=$("profile-form"), p=profile(); form.reset();
  if(p) for(const name of ["profile_name","desired_role","career_months","preferred_locations","preferred_employment_types","skills","certifications"]) form.elements[name].value=Array.isArray(p[name])?p[name].join(", "):p[name]??"";
  updateProfileUI();
}
async function loadProfiles() {
  state.profiles=(await api("/api/profiles")).profiles;
  if(!state.profiles.some(p=>p.id===state.profileId)) state.profileId=null;
  fillProfile();await loadExperiences();
}
async function chooseProfile(id) {
  if(state.busy) {$("profile-select").value=state.profileId||"";return toast("대화 처리가 끝난 뒤 프로필을 변경해 주세요.",true);}
  state.profileId=id; storeProfile(id);resetExperienceCoach();resetExperienceForm();fillProfile();resetChat();newPlan();
  await Promise.all([loadGrowth(),loadPlans(),loadExperiences()]);if(state.pendingDraft&&profile())importPlanDraft(state.pendingDraft);
}
$("profile-select").addEventListener("change",()=>chooseProfile(Number($("profile-select").value)||null).catch(e=>toast(e.message,true)));
$("new-profile").addEventListener("click",()=>chooseProfile(null).catch(e=>toast(e.message,true)));
$("profile-form").addEventListener("submit",async event=>{
  event.preventDefault(); if(state.busy)return toast("대화 처리가 끝난 뒤 저장해 주세요.",true);
  const form=event.target, button=$("save-profile"), old=profile();
  const data={profile_name:form.elements.profile_name.value.trim()||null,initial_experiences:state.profileId?[]:(state.initialExperiences||[]),desired_role:form.elements.desired_role.value.trim(),career_months:form.elements.career_months.value===""?null:Number(form.elements.career_months.value),education_level:old?.education_level??null,education_status:old?.education_status??null,major:old?.major??null};
  for(const field of ["preferred_locations","preferred_employment_types","skills","certifications"])data[field]=listValue(form.elements[field].value);
  button.disabled=true;
  try {const p=await api(state.profileId?`/api/profiles/${state.profileId}`:"/api/profiles",send(state.profileId?"PUT":"POST",data));state.initialExperiences=[];state.profileId=p.id;storeProfile(p.id);resetExperienceCoach();resetExperienceForm();await loadProfiles();await Promise.all([loadGrowth(),loadPlans()]);resetChat();toast("프로필을 저장했습니다. AI 상담에서 나에게 맞는 공고를 찾아보세요.");if(state.pendingDraft)importPlanDraft(state.pendingDraft);}
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
  const bubble=document.createElement("div");bubble.className="bubble";bubble.textContent=text;message.append(label,bubble);$("messages").append(message);$("messages").scrollTop=$("messages").scrollHeight;return message;
}
const welcomeHTML=$("messages").innerHTML;
function resetChat() {
  if(state.busy)return;
  clearRequirements();state.fitFollowUp=null;renderFitFollowUp();
  state.drafts=[];state.sessionId=freshId();state.traceSession=null;state.jobs=[];$("messages").innerHTML=welcomeHTML;$("job-results").innerHTML="";$("log-session").value="";$("message-input").value="";renderTrace([]);setRunStatus("neutral","대기");
}
$("new-chat").addEventListener("click",()=>{resetChat();toast("새 대화를 시작합니다.");});
function setRunStatus(kind,label){$("run-status").className=`badge ${kind}`;$("run-status").textContent=label;}
function setBusy(busy) {
  state.busy=busy;$("active-profile-select").disabled=busy;$("job-source").disabled=busy;$("original-confirmed").disabled=busy;$("requirements-complete").disabled=busy;$("cancel-requirements").disabled=busy;$("chat-loading").hidden=!busy;$("send-message").disabled=busy;$("new-chat").disabled=busy;$("message-input").disabled=busy;$("assess-fit").disabled=busy||!profile()||$("job-source").value==="incruit";$("delete-logs").disabled=busy||!state.traceSession;
}
$("messages").addEventListener("click",event=>{const prompt=event.target.closest("[data-prompt]");if(prompt){$("message-input").value=prompt.dataset.prompt;$("chat-form").requestSubmit();}});
$("message-input").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing){event.preventDefault();$("chat-form").requestSubmit();}});
$("chat-form").addEventListener("submit",async event=>{
  event.preventDefault();const text=$("message-input").value.trim();if(!text||state.busy)return;
  const selected=state.requirementsJob;
  if(selected&&!profile()){toast("프로필을 먼저 저장하거나 선택해 주세요.",true);navigate("profile");return;}
  if(selected&&!$("original-confirmed").checked)return toast("원문 공고를 확인하고 확인 항목에 체크해 주세요.",true);
  const period=selected?$("recruitment-period").value:null;
  if(selected&&!period)return toast("모집 기간을 입력하거나 확인하지 못한 상태를 선택해 주세요.",true);
  if(period==="provided"&&(!$("recruitment-start").value||!$("recruitment-end").value||$("recruitment-end").value<$("recruitment-start").value))return toast("모집 시작일과 종료일을 순서대로 입력해 주세요.",true);
  addMessage("user",selected?`[${selected.title} · 자격요건 입력]\n${text}`:text);$("message-input").value="";setBusy(true);setRunStatus("running","처리 중");
  try {
    const result=await api("/api/chat",send("POST",{message:text,session_id:state.sessionId,profile_id:state.profileId,assess_fit:!!profile()&&$("assess-fit").checked,job_source:$("job-source").value,requirements_job_id:selected?.id||null,original_confirmed:!!selected&&$("original-confirmed").checked,requirements_complete:!!selected&&$("requirements-complete").checked,recruitment_period_status:period,recruitment_start:period==="provided"?$("recruitment-start").value:null,recruitment_end:period==="provided"?$("recruitment-end").value:null}));
    if(selected){clearRequirements();toast("입력한 요건으로 준비 상담을 시작했습니다.");}
    const answerMessage=addMessage("assistant",result.answer);if(result.plan_draft)showPlanDraft(result.plan_draft,answerMessage);if(result.experience_draft)showExperienceDraft(result.experience_draft,answerMessage);updateFitFollowUp(result);renderJobs(result);$("log-session").value="";state.traceSession=result.session_id;renderTrace(result.events);setRunStatus("completed","완료");
    const missing=result.tool_calls.find(call=>call.error_code==="MISSING_API_KEY");if(missing)addMessage("assistant",`${toolNames[missing.tool]}에 필요한 API 키가 없습니다. .env의 ${["search_jobs","get_job_detail"].includes(missing.tool)?"ALIO_API_KEY":"DATA_GO_KR_KEY"} 설정을 확인해 주세요.`,true);
    if(result.used_previous_context)toast("이전 조건을 유지하고 변경한 조건을 반영했습니다.");
    await refreshLogs();
  } catch(error){if(selected)$("message-input").value=text;addMessage("assistant",error.message,true);renderTrace(error.data?.events||[]);setRunStatus("failed","실패");toast(error.message,true);}
  finally {setBusy(false);$("message-input").focus();}
});
function renderTrace(events) {
  if(!events.length){state.traceSession=null;$("delete-logs").disabled=true;$("trace").innerHTML=`<div class="trace-empty">${icon("log")}<strong>추천의 과정을 확인하세요</strong><p>실행 단계와 도구 호출,<br>재계획 이유를 순서대로 보여드려요.</p></div>`;return;}
  let lastRun=null;
  $("trace").innerHTML=events.map(event=>{
    const p=event.payload||{},s=stages[p.stage]||p.stage||"",tool=toolNames[p.tool]||p.tool||"";
    const labels={run_started:"요청 시작",run_finished:"요청 종료",stage_started:`${s} 진입`,stage_finished:`${s} 종료`,intent_decided:"의도 판단",plan_created:p.stage==="respond"?(p.missing_count?"일정 추가 입력 확인":"일정 초안 구성"):"실행 순서 구성",constraint_decided:"조건 충족 판단",route_decided:`${stages[p.next_stage]||"다음 단계"} 선택`,replan_requested:`재계획 ${p.replan_count||""}회`,tool_requested:`${tool} 요청`,tool_finished:`${tool} 응답`,fit_decided:"경험과 준비 상황 확인",error:"처리 오류"};
    const header=event.run_id!==lastRun?`<div class="trace-run">실행 ${esc(event.run_id.slice(0,8))}</div>`:"";lastRun=event.run_id;
    const reason=p.reason||(["tool_finished","constraint_decided"].includes(event.event_type)?[p.status?statusNames[p.status]:null,p.count!=null?`${p.count}건`:null,p.deadline_status==="not_requested"?"마감 조건 없음":p.deadline_status==="unmet"?"마감 조건 미충족":p.deadline_status==="unknown"?"마감일 확인 필요":null].filter(Boolean).join(" · "):"");
    const type=event.event_type==="replan_requested"?"replan":event.event_type==="error"||p.status==="error"?"error":"";
    return `${header}<div class="trace-item ${type}">${event.duration_ms!=null?`<span class="duration">${event.duration_ms}ms</span>`:""}<strong>${esc(labels[event.event_type]||event.event_type)}</strong>${reason?`<small>${esc(reason)}</small>`:""}${p.filter_status==="unverified"?"<small>고용형태·NCS 조건 미확인</small>":""}${p.availability_status==="unknown"?"<small>RSS 공고 진행 상태 미확인</small>":""}${p.source?`<small>${p.source==="all"?"통합 검색":sourceName(p.source)}</small>`:""}${p.duplicate_count?`<small>인크루트 중복 ${p.duplicate_count}건 제외</small>`:""}${p.error_code?`<small>${esc(p.error_code==="MISSING_API_KEY"?"API 키 설정 필요":p.error_code)}</small>`:""}${p.data_source?`<small>${p.data_source==="external_api"?"외부 API 조회":p.data_source==="rss"?"인크루트 RSS 수집":p.data_source==="mixed"?"여러 출처 통합 조회":"확보한 후보 재사용"}</small>`:""}</div>`;
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
function searchMatchHTML(job) {
  const match=job.search_match;
  if(!match?.rank||!match.category_labels?.length)return "";
  const related=match.level==="related";
  return `<div class="search-match ${related?"related":"direct"}"><strong>${related?"분야 관련 후보 · 직무 확인 필요":"제목의 직무 표현 일치"}</strong><span>${match.category_labels.map(esc).join(" · ")}</span><p>${(match.reasons||[]).map(esc).join(" ")}</p></div>`;
}
function atsNoteHTML(job) {
  if(!["greenhouse","lever"].includes(job.source))return "";
  const labels={employment_type:"고용형태",career_type:"경력",ncs_category:"NCS"};
  const unknown=(job.unverified_filters||[]).map(key=>labels[key]).filter(Boolean);
  return `<p class="rss-card-note">설정된 국내 기업의 공개 채용공고 · 마감일과 최종 요건은 원문 확인${unknown.length?`<br>요청한 ${unknown.map(esc).join("·")} 조건은 확인되지 않은 참고 후보입니다.`:""}</p>`;
}
function renderJobs(result) {
  if(result.reference_status==="unsupported_reference_date"){state.jobs=[];$("job-results").innerHTML='<div class="notice">요청한 기준일의 진행 이력을 확인할 수 없습니다. 현재 공고로 대체하지 않았습니다.</div>';return;}
  if(!result.analysis.intents.some(i=>["search_jobs","get_job_detail"].includes(i))&&result.analysis.evidence_source!=="user_submitted")return;
  state.jobs=result.jobs;
  const failed=(result.source_results||[]).filter(row=>row.status==="error");
  const sourceNotice=failed.length?`<div class="notice warning">${failed.map(row=>sourceName(row.source)).join(", ")} 조회 실패. 확보한 결과만 보여드리며 전체 0건으로 단정하지 않습니다.</div>`:"";
  const removed=(result.source_results||[]).reduce((sum,row)=>sum+(row.duplicate_count||0),0);
  const duplicateNotice=removed?`<div class="notice">수집한 인크루트 RSS와 중복된 Greenhouse·Lever 공고 ${removed}건을 제외했습니다.</div>`:"";
  if(!result.jobs.length){
    const status=result.search_summary?.search_status;
    $("job-results").innerHTML=sourceNotice+duplicateNotice+`<div class="notice">${status==="no_result"?"수집 범위에서 검색 결과 0건입니다. 더 넓은 직무 검색어로 찾아보세요.":status==="error"?"일부 또는 전체 출처를 조회하지 못했습니다. 실행 기록을 확인해 주세요.":"요청한 조건을 충족한다고 확인한 공고가 없습니다."}</div>`;
  } else {
    $("job-results").innerHTML=sourceNotice+duplicateNotice+`<div class="results-heading"><h3>함께 살펴볼 기회 <span class="count">${result.jobs.length}</span></h3><span class="results-note">${result.jobs.some(job=>job.search_match?.rank>0&&job.search_match?.category_labels?.length)?"공통 직무 기준 · 제목 근거 우선 · 분야만 일치하면 관련 후보로 표시":"경험을 연결하고 다음 준비를 정해 보세요"}</span></div><div class="job-grid">${result.jobs.map(job=>{
      const match=result.matches.find(m=>m.job_id===job.id),url=safeUrl(job.job_url),scored=match?.current&&match?.future;
      const meta=[...(job.location||[]).slice(0,6),...((job.location||[]).length>6?[`외 ${job.location.length-6}개 지역`]:[]),...(job.employment_type||[]),job.deadline?`마감 ${formatDate(job.deadline)}`:"마감일 원문 확인 필요"];
      const reference=scored?`<details class="reference-fit"><summary>참고 적합도와 확인 근거 펼쳐보기</summary><div class="scores">${scoreBlock(match.current,"현재 참고 적합도")}${scoreBlock(match.future,"미래 예상",true)}</div><div class="eligibility-line">현재 필수조건 ${badge(match.current.eligibility)}</div><div class="eligibility-line">미래 필수조건 ${badge(match.future.eligibility)}</div><p>경험에서 더 확인할 역량: ${esc(match.current.gaps?.join(", ")||"없음")}</p><p>추가 확인: ${esc([...(match.current.unknown||[]),...(match.current.blockers||[])].join(", ")||"없음")}</p><p>반영한 성장 계획: ${esc(match.applied_plans?.join(", ")||"없음")}</p>${match.other_preferred?.length?`<p>기타 우대조건: ${esc(match.other_preferred.join(", "))} (기술 점수와 별도)</p>`:""}<p>점수 세부 기준: ${esc(Object.entries(match.current.by_criterion||{}).map(([k,v])=>`${k} ${v??"미확인"}`).join(" · "))}</p></details>`:"";
      return `<article class="job-card"><div class="job-company"><span>${esc(job.company||"채용 기관")}</span><span class="badge neutral">${sourceName(job.source)}</span></div><h4>${esc(job.title)}</h4>${searchMatchHTML(job)}<div class="job-meta">${meta.map(value=>`<span>${esc(value)}</span>`).join("")}</div>${atsNoteHTML(job)}${job.recruitment_start?`<p class="reference-note">사용자 입력 모집 기간: ${esc(job.recruitment_start)} ~ ${esc(job.deadline)}</p>`:""}${job.source==="incruit"?`<p class="rss-card-note">RSS 일부 공고 · 진행 상태·고용형태·NCS 조건은 원문 확인 필요</p>${job.rss_summary?`<details class="rss-details"><summary>RSS 요약 보기</summary><p class="rss-summary">${esc(job.rss_summary)}</p></details>`:""}`:""}${match?.evidence_source==="user_submitted"?`<p class="rss-card-note">사용자 입력 요건 기준 · 원문 일치 미검증</p>`:""}${match?.requires_experience_follow_up?'<p class="experience-needed">관련 경험을 더 확인하고 있습니다. 추가 경험이 없다면 상담에서 알려 주세요.</p>':scored?'<p class="preparation-note">확인된 경험을 바탕으로 준비할 일을 정리해 보세요. 점수는 참고 정보로만 확인할 수 있어요.</p>':`<p class="reference-note">${job.source==="incruit"?"원문에서 모집 기간과 요건을 확인한 뒤 입력하면 준비 상담을 도와드려요.":match?.error?"이 공고의 준비 상황을 확인하지 못했습니다.":profile()?"경험 기반 준비 상담을 켜고 관련 경험을 함께 살펴보세요.":"프로필과 경험을 저장하면 나에게 필요한 준비를 함께 살펴볼 수 있어요."}</p>`}${reference}<div class="job-actions">${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">원문 공고 보기</a>`:""}${job.source==="incruit"?`<button class="button secondary" data-job-requirements="${job.id}">기간·자격요건 입력</button>`:""}<button class="button secondary" data-job-plan="${job.id}">${icon("calendar")}준비 계획 만들기</button></div></article>`;
    }).join("")}</div><p class="reference-note">목표는 경험을 정리하고 다음 준비를 돕는 것입니다. 참고 점수는 합격 확률이나 지원 여부의 판정이 아닙니다.</p>`;
  }
  if(result.search_suggestions?.length)$("job-results").insertAdjacentHTML("beforeend",`<div class="search-suggestions"><strong>이 검색어로 넓혀 볼까요?</strong>${result.search_suggestions.map(term=>`<button type="button" class="button secondary" data-suggest="${esc(term)}">${esc(term)}</button>`).join("")}</div>`);
  if(result.excluded_jobs.length)$("job-results").insertAdjacentHTML("beforeend",`<details class="excluded-list"><summary>조건 확인이 필요한 공고 ${result.excluded_jobs.length}건</summary><ul>${result.excluded_jobs.map(job=>`<li>${esc(job.title)} · ${job.deadline_status==="unmet"?"요청한 마감 조건 미충족":"마감일 미확인"}</li>`).join("")}</ul></details>`);
}

$("job-results").addEventListener("click",event=>{const button=event.target.closest("[data-job-plan]");if(!button)return;if(!profile()){toast("준비 계획을 저장하려면 프로필을 먼저 입력해 주세요.",true);navigate("profile");return;}const job=state.jobs.find(j=>j.id===Number(button.dataset.jobPlan));newPlan(job);navigate("plans");$("plan-form").elements.title.focus();});
async function init() {
  state.profileId=storedProfile();newPlan();navigate(location.hash.slice(1)||"chat");
  try {await api("/health");$("server-status").textContent="서버 연결됨";$("server-dot").classList.add("connected");await loadProfiles();await Promise.all([loadGrowth(),loadPlans(),refreshLogs()]);}
  catch(error){$("server-status").textContent="연결 확인 필요";toast(error.message,true);}
}
window.addEventListener("resize",()=>requestAnimationFrame(()=>{$("messages").scrollTop=$("messages").scrollHeight;}));
init();

function showPlanDraft(draft,message) {
  const index=state.drafts.push(draft)-1;
  const button=document.createElement("button");button.type="button";button.className="button secondary small";button.textContent="지원 계획으로 가져오기";
  button.addEventListener("click",()=>importPlanDraft(state.drafts[index]));message.append(button);
}
function importPlanDraft(draft) {
  if(!profile()){state.pendingDraft=draft;navigate("profile");toast("프로필을 저장하거나 선택하면 일정 초안을 가져옵니다.");return;}
  state.pendingDraft=null;newPlan();
  const form=$("plan-form");
  for(const key of ["title","plan_type","status","due_date","notes","growth_plan_id"])form.elements[key].value=draft[key]??"";
  form.elements.start_at.value=localInput(draft.start_at);form.elements.end_at.value=localInput(draft.end_at);
  if(draft.job_posting_id){state.linkedJob={id:draft.job_posting_id};$("linked-job").hidden=false;$("linked-job").textContent="추천 공고와 연결된 일정 초안";}
  $("schedule-kind").value=draft.start_at?"timed":"date";scheduleFields();
  $("plan-form-title").textContent="AI 일정 초안 검토";$("save-plan").textContent="검토한 계획 저장";
  navigate("plans");toast("아직 저장하지 않았습니다. 날짜와 시간을 확인한 뒤 저장해 주세요.");
}

function clearRequirements() {
  state.requirementsJob=null;resetCapture();$("recruitment-period").required=false;$("recruitment-period").value="";$("recruitment-start").value="";$("recruitment-end").value="";$("recruitment-dates").hidden=true;$("period-caption").textContent="";$("requirements-target").hidden=true;$("original-confirmed").checked=false;$("requirements-complete").checked=false;
  $("message-input").placeholder="예: 강원 지역 정규직 약사 공고를 찾아줘";
}
$("cancel-requirements").addEventListener("click",()=>{if(state.busy)return;clearRequirements();$("message-input").value="";});
$("job-results").addEventListener("click",event=>{
  const button=event.target.closest("[data-job-requirements]");if(!button||state.busy)return;
  if(!profile()){toast("적합도 평가를 위해 프로필을 먼저 저장하거나 선택한 뒤 공고를 다시 검색해 주세요.",true);navigate("profile");return;}
  const job=state.jobs.find(item=>item.id===Number(button.dataset.jobRequirements));if(!job||job.source!=="incruit")return;
  clearRequirements();state.requirementsJob=job;$("recruitment-period").required=true;$("requirements-target").hidden=false;$("requirements-title").textContent=`자격요건 입력: ${job.title}`;
  const url=safeUrl(job.job_url);$("requirements-link").hidden=!url;if(url)$("requirements-link").href=url;
  $("message-input").value="";$("message-input").placeholder="필수: Python, SQL 활용 가능\n우대: FastAPI 경험, 정보처리기사\n경력: 원문에 명시된 경우 입력";
  $("requirements-target").scrollIntoView({behavior:"smooth",block:"center"});$("message-input").focus();
});

Object.assign(state,{initialExperiences:[],experiences:[],experienceId:null,coachMessages:[],coachDraft:null,coachSession:freshId(),captureImages:[],fitFollowUp:null});

$("active-profile-select").addEventListener("change",()=>chooseProfile(Number($("active-profile-select").value)||null).catch(error=>toast(error.message,true)));
function updateExperienceOwner() {
  const owner=profile();
  $("experience-owner").textContent=owner?`${owner.profile_name||owner.desired_role} · #${owner.id}에 저장할 경험` :"새 프로필에 담을 경험 · 프로필 저장 시 함께 저장됩니다";
  $("save-experience").textContent=owner?"확인한 경험 저장":"경험을 새 프로필에 담기";
  $("initial-experience-note").textContent=!owner&&state.initialExperiences?.length?`경험 ${state.initialExperiences.length}건을 프로필과 함께 저장합니다.`:"새 프로필은 기존 프로필과 별도로 저장됩니다.";
  $("experience-recheck").hidden=!state.fitFollowUp||!owner;
}
async function loadExperiences() {
  const id=state.profileId;
  const result=id?await api(`/api/profiles/${id}/experiences`):{experiences:[]};
  if(id!==state.profileId)return;
  state.experiences=result.experiences;renderExperiences();updateExperienceOwner();
}
function resetExperienceForm() {
  state.experienceId=null;$("experience-form").reset();$("experience-form-title").textContent="경험 검토·입력";updateExperienceOwner();
}
$("new-experience").addEventListener("click",resetExperienceForm);
function renderExperiences() {
  const items=state.profileId?state.experiences:(state.initialExperiences||[]);
  $("experience-total").textContent=items.length;
  $("experience-list").innerHTML=items.length?items.map((item,index)=>`<article class="panel experience-card"><div class="results-heading"><div><h4>${esc(item.title)}</h4><p>${esc(item.role||"")} ${esc(item.organization||"")}</p></div><span class="badge soft">${state.profileId?"저장됨":"새 프로필 저장 대기"}</span></div><p>${esc(item.description||"")}</p><p>확인한 사용 기술: ${esc((item.skills||[]).join(", ")||"미입력")}</p><details><summary>정리한 경험 보기</summary>${[["상황",item.situation],["과제",item.task],["행동",item.action],["결과·배운 점",item.result]].map(([label,value])=>`<p><strong>${label}</strong> ${esc(value||"아직 정리하지 않음")}</p>`).join("")}</details><div class="job-actions"><button class="button secondary" data-edit-experience="${state.profileId?item.id:`draft-${index}`}">경험 수정</button><button class="text-button danger" data-delete-experience="${state.profileId?item.id:`draft-${index}`}">경험 삭제</button></div></article>`).join(""):'<div class="notice">아직 정리한 경험이 없습니다. AI와 이야기하거나 직접 입력해 프로필에 담아 보세요.</div>';
}
$("experience-form").addEventListener("submit",async event=>{
  event.preventDefault();if(state.busy)return toast("대화 처리가 끝난 뒤 경험을 저장해 주세요.",true);
  const form=event.target,data={experience_type:form.elements.experience_type.value,skills:listValue(form.elements.skills.value)};
  for(const key of ["title","organization","role","start_date","end_date","description","situation","task","action","result"])data[key]=form.elements[key].value.trim()||null;
  if(data.start_date&&data.end_date&&data.end_date<data.start_date)return toast("경험 종료일이 시작일보다 빠릅니다.",true);
  const id=state.profileId,button=$("save-experience");button.disabled=true;
  try {
    if(id){
      await api(`/api/profiles/${id}/experiences${state.experienceId?`/${state.experienceId}`:""}`,send(state.experienceId?"PUT":"POST",data));
      if(id!==state.profileId)return;
      await loadExperiences();toast("경험을 저장했습니다. 상담에서 사용 기술과 경험을 함께 반영합니다.");
    } else {
      const index=typeof state.experienceId==="string"?Number(state.experienceId.replace("draft-","")):null;
      if(index!==null)state.initialExperiences[index]=data;else state.initialExperiences.push(data);
      renderExperiences();toast("새 프로필에 경험을 담았습니다. 프로필 저장을 눌러 함께 저장해 주세요.");
    }
    resetExperienceForm();
  } catch(error){toast(error.message,true);}finally{button.disabled=false;}
});
$("experience-list").addEventListener("click",async event=>{
  const edit=event.target.closest("[data-edit-experience]"),remove=event.target.closest("[data-delete-experience]");if((!edit&&!remove)||state.busy)return;
  const key=(edit||remove).dataset[edit?"editExperience":"deleteExperience"],draft=key.startsWith("draft-"),index=draft?Number(key.slice(6)):null;
  const item=draft?state.initialExperiences[index]:state.experiences.find(value=>value.id===Number(key));if(!item)return;
  if(edit){resetExperienceForm();state.experienceId=draft?key:Number(key);const form=$("experience-form");for(const name of ["title","experience_type","organization","role","start_date","end_date","description","situation","task","action","result"])form.elements[name].value=item[name]||"";form.elements.skills.value=(item.skills||[]).join(", ");$("experience-form-title").textContent="경험 수정";form.scrollIntoView({behavior:"smooth",block:"start"});}
  if(remove){
    if(!await confirmDelete("경험을 삭제할까요?",item.title))return;
    try{if(draft){state.initialExperiences.splice(index,1);renderExperiences();}else{await api(`/api/profiles/${state.profileId}/experiences/${key}`,{method:"DELETE"});await loadExperiences();}resetExperienceForm();toast("경험을 삭제했습니다.");}catch(error){toast(error.message,true);}
  }
});
function resetExperienceCoach() {
  state.coachMessages=[];state.coachDraft=null;state.coachSession=freshId();$("experience-chat-input").value="";
  $("experience-messages").innerHTML='<p>무엇을 만들거나 해결했는지 편하게 알려 주세요. 경험의 사실을 함께 정리해요.</p>';
  $("experience-draft-info").textContent="";$("import-experience-draft").hidden=true;
}
$("reset-experience-coach").addEventListener("click",resetExperienceCoach);
function coachMessage(role,text) {
  const element=document.createElement("div");element.className=`coach-message ${role}`;element.textContent=`${role==="user"?"나":"경험 코치"}: ${text}`;$("experience-messages").append(element);$("experience-messages").scrollTop=$("experience-messages").scrollHeight;
}
$("experience-chat-form").addEventListener("submit",async event=>{
  event.preventDefault();if(state.busy)return toast("현재 상담이 끝난 뒤 경험을 정리해 주세요.",true);
  const message=$("experience-chat-input").value.trim();if(!message)return;
  const owner=state.profileId,session=state.coachSession,history=[...state.coachMessages.slice(-18),{role:"user",content:message}];
  const button=$("send-experience-chat");button.disabled=true;button.textContent="경험을 정리하고 있어요…";
  try{
    const result=await api("/api/experience-coach",send("POST",{session_id:session,messages:history}));
    if(owner!==state.profileId||session!==state.coachSession)return;
    coachMessage("user",message);coachMessage("assistant",result.reply);state.coachMessages=[...history,{role:"assistant",content:result.reply}];state.coachDraft=result.draft;
    $("experience-chat-input").value="";$("import-experience-draft").hidden=!result.draft;$("experience-draft-info").textContent=result.missing_fields?.length?`더 확인할 내용: ${result.missing_fields.join(", ")}`:"초안은 직접 확인하고 저장해 주세요.";
  }catch(error){toast(error.message,true);}finally{button.disabled=false;button.textContent="경험 정리 도움받기";}
});
$("import-experience-draft").addEventListener("click",()=>{
  if(!state.coachDraft)return;resetExperienceForm();const form=$("experience-form");
  for(const name of ["title","experience_type","organization","role","start_date","end_date","description","situation","task","action","result"])form.elements[name].value=state.coachDraft[name]||"";
  form.elements.skills.value=(state.coachDraft.skills||[]).join(", ");form.scrollIntoView({behavior:"smooth",block:"start"});toast("아직 저장하지 않았습니다. 경험과 기술을 확인한 뒤 저장해 주세요.");
});

function updateFitFollowUp(result) {
  if(result.fit_follow_up)state.fitFollowUp=result.fit_follow_up;
  else if(result.matches?.length||result.analysis?.intents?.some(name=>["search_jobs","get_job_detail"].includes(name)))state.fitFollowUp=null;
  renderFitFollowUp();updateExperienceOwner();
}
function renderFitFollowUp() {
  const panel=$("fit-followup");panel.hidden=!state.fitFollowUp;
  if(!state.fitFollowUp){panel.innerHTML="";return;}
  panel.innerHTML=`<strong>점수보다 먼저, 경험을 더 살펴볼까요?</strong><ul>${state.fitFollowUp.questions.map(question=>`<li>${esc(question)}</li>`).join("")}</ul><div class="job-actions"><button id="add-followup-experience" class="button primary" type="button">경험 추가·정리</button><button id="no-more-experience" class="button secondary" type="button">추가 경험 없음 · 준비 방향 보기</button></div>`;
}
$("fit-followup").addEventListener("click",event=>{
  if(state.busy)return;
  if(event.target.closest("#add-followup-experience")){navigate("profile");$("experience-chat-input").placeholder="관련 경험에서 내가 맡은 일과 실제 사용한 기술을 알려 주세요.";$("experience-chat-input").scrollIntoView({behavior:"smooth",block:"center"});$("experience-chat-input").focus();}
  if(event.target.closest("#no-more-experience"))sendFitAction("none");
});
async function sendFitAction(action) {
  if(state.busy||!state.fitFollowUp)return;
  setBusy(true);setRunStatus("running","처리 중");navigate("chat");
  const message=action==="none"?"추가 경험 없음":"저장한 경험으로 준비 상담을 이어가 주세요.";addMessage("user",message);
  try{
    const result=await api("/api/chat",send("POST",{message,session_id:state.sessionId,profile_id:state.profileId,fit_follow_up_action:action}));
    addMessage("assistant",result.answer);updateFitFollowUp(result);renderJobs(result);state.traceSession=result.session_id;renderTrace(result.events);setRunStatus("completed","완료");await refreshLogs();
  }catch(error){addMessage("assistant",error.message,true);toast(error.message,true);setRunStatus("failed","실패");}finally{setBusy(false);}
}
$("experience-recheck").addEventListener("click",()=>sendFitAction("recheck"));
$("job-results").addEventListener("click",event=>{
  const button=event.target.closest("[data-suggest]");if(!button||state.busy)return;
  $("message-input").value=`검색어를 ${button.dataset.suggest}로 바꿔서 다시 찾아줘.`;$("chat-form").requestSubmit();
});

function resetCapture() {
  state.captureImages=[];$("capture-files").value="";$("capture-previews").innerHTML="";$("read-capture").disabled=true;$("capture-status").textContent="";
}
$("recruitment-period").addEventListener("change",()=>{
  const provided=$("recruitment-period").value==="provided";$("recruitment-dates").hidden=!provided;
  if(!provided){$("recruitment-start").value="";$("recruitment-end").value="";}
});
async function addCapture(files) {
  if(state.busy||!state.requirementsJob)return;
  for(const file of files){
    if(state.captureImages.length>=2){toast("캡처는 최대 2장 첨부할 수 있어요.",true);break;}
    if(!["image/png","image/jpeg"].includes(file.type)||file.size>10*1024*1024){toast("PNG/JPG 이미지를 10MB 이하로 선택해 주세요.",true);continue;}
    try{
      const bitmap=await createImageBitmap(file),ratio=Math.min(1,2048/Math.max(bitmap.width,bitmap.height));
      const canvas=document.createElement("canvas");canvas.width=Math.round(bitmap.width*ratio);canvas.height=Math.round(bitmap.height*ratio);canvas.getContext("2d").drawImage(bitmap,0,0,canvas.width,canvas.height);bitmap.close();
      const value=canvas.toDataURL("image/jpeg",0.9);if(value.length>4_000_000)throw new Error("이미지가 큽니다. 필요한 부분만 캡처해 주세요.");
      state.captureImages.push(value);
    }catch(error){toast(error.message||"이미지를 읽지 못했습니다.",true);}
  }
  renderCapture();
}
function renderCapture() {
  $("capture-previews").innerHTML=state.captureImages.map((value,index)=>`<div><img src="${value}" alt="첨부 캡처 ${index+1}"><button class="text-button" type="button" data-remove-capture="${index}">캡처 ${index+1} 삭제</button></div>`).join("");
  $("read-capture").disabled=!state.captureImages.length||state.busy;
}
$("capture-files").addEventListener("change",event=>addCapture(event.target.files));
$("capture-previews").addEventListener("click",event=>{const button=event.target.closest("[data-remove-capture]");if(button&&!state.busy){state.captureImages.splice(Number(button.dataset.removeCapture),1);renderCapture();}});
$("message-input").addEventListener("paste",event=>{
  if(!state.requirementsJob)return;
  const files=Array.from(event.clipboardData?.items||[]).filter(item=>item.kind==="file"&&item.type.startsWith("image/")).map(item=>item.getAsFile()).filter(Boolean);
  if(files.length){event.preventDefault();addCapture(files);}
});
$("read-capture").addEventListener("click",async()=>{
  if(!state.captureImages.length||state.busy)return;
  const jobId=state.requirementsJob?.id;setBusy(true);$("read-capture").disabled=true;$("capture-status").textContent="캡처에서 요건과 모집 기간을 읽고 있어요…";
  try{
    const draft=await api("/api/requirements/capture",send("POST",{images:state.captureImages}));
    if(state.requirementsJob?.id!==jobId)return;
    const extracted=[draft.required?`필수: ${draft.required}`:"",draft.preferred?`우대: ${draft.preferred}`:""].filter(Boolean).join("\n");
    const combined=[$("message-input").value.trim(),extracted].filter(Boolean).join("\n"),truncated=combined.length>4000;
    $("message-input").value=combined.slice(0,4000);
    const fullPeriod=draft.recruitment_start&&draft.recruitment_end;
    $("recruitment-period").value=fullPeriod?"provided":draft.period_status==="until_filled"?"until_filled":"unknown";
    $("recruitment-dates").hidden=!fullPeriod;$("recruitment-start").value=draft.recruitment_start||"";$("recruitment-end").value=draft.recruitment_end||"";
    $("period-caption").textContent=draft.period_text?`캡처의 모집 기간 문구: ${draft.period_text}`:"모집 기간이 읽히지 않으면 원문을 확인하고 직접 입력해 주세요.";
    $("capture-status").textContent=`검토용 초안을 입력란에 넣었습니다. 원문과 비교해 수정한 뒤 보내 주세요.${truncated?" 4,000자를 초과해 일부만 옮겼습니다. 필수요건을 중심으로 나누어 확인해 주세요.":""}${draft.missing_fields?.length?` 더 확인: ${draft.missing_fields.join(", ")}`:""}`;
    $("original-confirmed").checked=false;$("requirements-complete").checked=false;
  }catch(error){$("capture-status").textContent=error.message;toast(error.message,true);}finally{setBusy(false);renderCapture();}
});

function showExperienceDraft(draft,message) {
  const owner=state.profileId,button=document.createElement("button");button.type="button";button.className="button secondary";button.textContent="경험 초안 확인·저장하기";
  button.addEventListener("click",()=>{if(owner!==state.profileId)return toast("경험을 이야기한 프로필을 다시 선택해 주세요.",true);state.coachDraft=draft;navigate("profile");$("import-experience-draft").click();});message.append(button);
}
