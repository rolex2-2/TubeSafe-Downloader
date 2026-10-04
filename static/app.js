const $ = id => document.getElementById(id);
let timer = null;

function show(el){el.classList.remove("hidden")}
function hide(el){el.classList.add("hidden")}
function error(msg){$("error").textContent=msg;show($("error"))}

$("infoBtn").onclick = async () => {
  hide($("error"));
  const url = $("url").value.trim();
  if(!url) return error("Enter a YouTube URL.");
  $("infoBtn").disabled = true;
  $("infoBtn").textContent = "Checking...";
  try{
    const r = await fetch("/api/info",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url})});
    const d = await r.json();
    if(!r.ok) throw new Error(d.error || "Unable to read video information.");
    $("thumb").src=d.thumbnail || "";
    $("title").textContent=d.title || "Video";
    $("meta").textContent=[d.uploader,d.duration].filter(Boolean).join(" • ");
    show($("preview"));
  }catch(e){error(e.message)}
  finally{$("infoBtn").disabled=false;$("infoBtn").textContent="Check"}
};

$("mode").onchange = () => {
  const isMp3 = $("mode").value === "mp3";
  $("quality").disabled = isMp3;
};

$("downloadBtn").onclick = async () => {
  hide($("error")); hide($("result"));
  const url=$("url").value.trim();
  if(!url) return error("Enter a YouTube URL.");
  const mode=$("mode").value, quality=$("quality").value;
  $("downloadBtn").disabled=true; show($("progressBox"));
  $("statusText").textContent="Starting...";
  $("percent").textContent="0%"; $("barFill").style.width="0%";
  try{
    const r=await fetch("/api/download",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url,mode,quality})});
    const d=await r.json();
    if(!r.ok) throw new Error(d.error || "Could not start download.");
    poll(d.job_id);
  }catch(e){error(e.message);$("downloadBtn").disabled=false}
};

async function poll(id){
  try{
    const r=await fetch("/api/status/"+id);
    const d=await r.json();
    $("statusText").textContent=d.message||"Working...";
    $("percent").textContent=Math.round(d.progress||0)+"%";
    $("barFill").style.width=Math.min(100,d.progress||0)+"%";
    if(d.status==="complete"){
      $("statusText").textContent="Complete";
      $("percent").textContent="100%";
      $("barFill").style.width="100%";
      $("result").innerHTML=`Your file is ready: <a href="/download/${encodeURIComponent(d.filename)}">Download ${escapeHtml(d.filename)}</a>`;
      show($("result")); $("downloadBtn").disabled=false; return;
    }
    if(d.status==="error") throw new Error(d.message||"Download failed.");
    timer=setTimeout(()=>poll(id),1000);
  }catch(e){error(e.message);$("downloadBtn").disabled=false}
}
function escapeHtml(s){return s.replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[m]))}
