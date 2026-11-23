"use client";
import { useCallback,useEffect,useRef,useState } from "react";
import { dayInZone, type Config } from "../lib/domain";
import type { BatchView, Preview } from "../lib/workspace";
import { samples } from "../lib/samples";
import { Campaign, ContextForm, Creations, PublishPanel, type Action } from "./studio";
import { Dashboard } from "./report-ui";

type Workspace = { ai:boolean; bytesLimit:number; durationLimit:number; batches:{id:string;name:string;published_at:string|null}[] };
const navigation = [
  ["dashboard","▤","Tổng quan"],["library","▱","Thư viện"],["upload","↑","Tải video"],
  ["review","☷","Duyệt & chỉnh sửa"],["campaign","◎","Chiến dịch"],["publish","↗","Xuất bản"],["settings","⚙","Tích hợp"],
] as const;
type View = (typeof navigation)[number][0];
const titles: Record<View,[string,string]> = {
  dashboard:["Hiệu suất chiến dịch","Theo dõi và so sánh kết quả mô phỏng theo lô, nhóm, video và copy."],
  library:["Thư viện sáng tạo","Tìm video, xem phân tích và đối chiếu các phiên bản copy đã lưu."],
  upload:["Tải video","Thêm video, bổ sung bối cảnh và theo dõi tiến độ độc lập của từng tệp."],
  review:["Duyệt & chỉnh sửa","Mỗi phiên bản copy đã chọn tạo một quảng cáo riêng cho video tương ứng."],
  campaign:["Cài đặt chiến dịch","Cấu hình một chiến dịch chung. Ngân sách không nhân theo số video."],
  publish:["Xuất bản demo","Xác nhận cấu hình và mô phỏng xuất bản. Không có quảng cáo nào được gửi tới Meta."],
  settings:["Tích hợp & cài đặt","Kiểm tra môi trường và điều kiện để sử dụng các provider."],
};
class ApiError extends Error { constructor(message:string,public status:number) {super(message);} }
async function api<T>(path:string, data?:unknown):Promise<T> {
  const response = await fetch("/api/"+path,data === undefined ? {cache:"no-store"} : {
    method:"POST",headers:{"content-type":"application/json","x-demo-request":"1"},body:JSON.stringify(data),
  });
  const value = await response.json();
  if (!response.ok) throw new ApiError(value.error || "Yêu cầu không thành công.",response.status);
  return value;
}
type Transfer = { id:string; name:string; percent:number; state:"waiting"|"uploading"|"done"|"error"; error?:string; file:File; gemini:boolean; batch:string };
function sendFile(batch:string,file:File,gemini:boolean,progress:(n:number)=>void) {
  return new Promise<void>((resolve,reject) => {
    const xhr = new XMLHttpRequest();
    const byExt:Record<string,string>={mp4:"video/mp4",mov:"video/quicktime",webm:"video/webm"};
    const mime = byExt[file.name.split(".").pop()?.toLowerCase() ?? ""];
    if (!mime) { reject(new Error("Định dạng phải là MP4, MOV hoặc WebM.")); return; }
    xhr.open("POST",`/api/batches/${batch}/upload`);
    xhr.setRequestHeader("Content-Type",mime);
    xhr.setRequestHeader("x-demo-request","1");
    xhr.setRequestHeader("x-video-name",encodeURIComponent(file.name));
    if (gemini) xhr.setRequestHeader("x-video-ai","gemini");
    xhr.upload.onprogress = e => { if(e.lengthComputable) progress(Math.round(e.loaded/e.total*100)); };
    xhr.timeout=190000;
    xhr.onerror=xhr.ontimeout=()=>reject(new Error("Mất kết nối. Hãy tải lại."));
    xhr.onload=()=> {
      if (xhr.status>=200 && xhr.status<300) resolve();
      else {
        let message = "Không tải được video.";
        try { message = JSON.parse(xhr.responseText).error || message; } catch {}
        reject(new Error(message));
      }
    };
    xhr.send(file);
  });
}
export default function Page() {
  const [workspace,setWorkspace]=useState<Workspace|null>();
  const [batchId,setBatchId]=useState("");
  const [batch,setBatch]=useState<BatchView>();
  const [preview,setPreview]=useState<Preview>();
  const [view,setView]=useState<View>("dashboard");
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [busy,setBusy]=useState(false);
  const [transfers,setTransfers]=useState<Transfer[]>([]);
  const [gemini,setGemini]=useState(false);
  const [newName,setNewName]=useState("");
  const [showNew,setShowNew]=useState(false);
  const [dragging,setDragging]=useState(false);
  const [reportScope,setReportScope]=useState("");
  const [focusVideo,setFocusVideo]=useState("");
  const [revision,setRevision]=useState(0);
  const active=useRef("");
  const refreshCounter=useRef(0);
  const busyRef=useRef(false);
  const uploader=useRef<HTMLInputElement>(null);
  const selectBatch=useCallback((key:string)=>{
    if (active.current===key) return;
    active.current=key;setBatchId(key);setBatch(undefined);setPreview(undefined);
    localStorage.setItem("mva-batch",key);
  },[]);
  const loadWorkspace=useCallback(async (select="")=>{
    const result=await api<Workspace>("workspace");
    setWorkspace(result);
    const wanted=select || active.current || localStorage.getItem("mva-batch");
    selectBatch(result.batches.find(b=>b.id===wanted)?.id || result.batches[0]?.id || "");
  },[selectBatch]);
  const refresh=useCallback(async (key:string)=>{
    if(!key) return;
    const order=++refreshCounter.current;
    const [b,p]=await Promise.all([api<BatchView>("batches/"+key),api<Preview>(`batches/${key}/preview`)]);
    if(active.current===key && order===refreshCounter.current) { setBatch(b);setPreview(p); }
  },[]);
  useEffect(()=>{ loadWorkspace().catch(e=>{
    if(e instanceof ApiError && e.status===401) setWorkspace(null);
    else {setError(e.message);setWorkspace(null);}
  }); },[loadWorkspace]);
  useEffect(()=>{
    if (!batchId) return;
    const reload=()=>refresh(batchId).catch(e=>{if(active.current===batchId) {
      setError(e.message);
      if(e instanceof ApiError && e.status===401) {setWorkspace(null);selectBatch("");}
    }});
    void reload();
    const timer=setInterval(reload,1300);
    return ()=>clearInterval(timer);
  },[batchId,refresh,selectBatch]);
  const mutate:Action=async (path,data,message)=>{
    if(busyRef.current)return false;
    busyRef.current=true;setBusy(true);setError("");setNotice("");
    try {
      const result=await api<{id?:string}>(path,data);
      if(path==="batches" || path==="session" || path.endsWith("/publish")) await loadWorkspace(result.id);
      await refresh(active.current);
      setRevision(prev=>prev+1);
      if(message)setNotice(message);
      return true;
    } catch(e) {setError(e instanceof Error ? e.message : "Có lỗi xảy ra.");return false;}
    finally {busyRef.current=false;setBusy(false);}
  };
  function patchTransfer(id:string,values:Partial<Transfer>) {
    setTransfers(list=>list.map(t=>t.id===id?{...t,...values}:t));
  }
  const uploading=transfers.some(t=>t.state==="uploading");
  useEffect(()=>{
    if (uploading) return;
    const t=transfers.find(t=>t.state==="waiting");
    if(!t) return;
    patchTransfer(t.id,{state:"uploading",percent:0,error:undefined});
    sendFile(t.batch,t.file,t.gemini,percent=>patchTransfer(t.id,{percent})).then(()=>{
      patchTransfer(t.id,{state:"done",percent:100});
      void refresh(t.batch).catch(()=>{});
    }).catch(e=>patchTransfer(t.id,{state:"error",error:e.message}));
  },[transfers,uploading,refresh]);
  function addFiles(files:FileList|File[]) {
    if(!batch || batch.published_at || !workspace)return;
    const slots=24-batch.videos.length-transfers.filter(t=>t.batch===batchId && ["waiting","uploading"].includes(t.state)).length;
    const selected=Array.from(files);
    if(selected.length>slots)setError("Chỉ có thể thêm tối đa 24 video trong mỗi lô.");
    setTransfers(list=>[...list,...selected.slice(0,Math.max(0,slots)).map((file):Transfer=>{
      const limit=gemini?Math.min(workspace.bytesLimit,12*1024*1024):workspace.bytesLimit;
      return {id:crypto.randomUUID(),batch:batchId,file,gemini,name:file.name,percent:0,
        state:file.size>limit?"error":"waiting",error:file.size>limit?"Video vượt dung lượng cho phép.":undefined};
    })]);
    if(uploader.current)uploader.current.value="";
  }
  function navigate(next:View) {
    setError("");setNotice("");setView(next);if(next!=="library")setFocusVideo("");window.scrollTo(0,0);
  }
  function openSource(nextBatch:string,video:string) {
    selectBatch(nextBatch);setFocusVideo(video);navigate("library");
  }
  const create=async ()=>{
    if(await mutate("batches",{name:newName},"Đã tạo lô mới.")) {setShowNew(false);setNewName("");setView("upload");}
  };
  const num=batch?.published_at ? batch.ads.length : preview?.plan.length ?? 0;
  const save=(part:string,data:unknown,message?:string)=>mutate(`batches/${batchId}/${part}`,data,message);
  if(workspace===undefined) return <main className="welcome"><p>Đang mở studio…</p></main>;
  if(!workspace) return <main className="welcome"><div className="welcome-card">
    <span className="mark">M</span><div className="eyebrow">STUDIO SÁNG TẠO</div><h1>Meta Video Ads</h1>
    <p className="lead">Từ một video đến chiến dịch hoàn chỉnh.</p><p>Thử toàn bộ quy trình với video và số liệu mẫu. Không cần tài khoản, token hay ngân sách Meta.</p>
    {error && <p className="alert danger" role="alert">{error}</p>}
    <button className="primary" disabled={busy} onClick={()=>void mutate("session",{})}>{busy?"Đang mở…":"Bắt đầu phiên demo"}</button>
    <small>Phiên này riêng tư theo trình duyệt và được lưu trong 30 ngày.</small>
  </div></main>;
  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><span className="mark">M</span><div><strong>Meta Video Ads</strong><small>STUDIO SÁNG TẠO</small></div></div>
      <nav aria-label="Điều hướng chính">{navigation.map(([key,icon,label])=>
        <button key={key} className={view===key?"active":""} aria-current={view===key?"page":undefined} onClick={()=>navigate(key)}>
          <span aria-hidden="true">{icon}</span>{label}
        </button>)}</nav>
      <div className="side-note"><span className="indicator"/>Không kết nối Meta<span>Asia/Ho_Chi_Minh · VND</span></div>
    </aside>
    <div className="main">
      <header className="topbar"><div className="mode"><span className="indicator"/> Chế độ demo</div>
        <div className="batch-select"><label htmlFor="active-batch">Lô hiện tại</label>
          <select id="active-batch" value={batchId} onChange={e=>{selectBatch(e.target.value);setFocusVideo("");}}>
            {workspace.batches.map(b=><option key={b.id} value={b.id}>{b.name}{b.published_at?" · đã khóa":""}</option>)}
          </select><button onClick={()=>setShowNew(!showNew)}>+ Lô mới</button>
        </div>
      </header>
      <main className="canvas">
        {showNew&&<form className="card inline" onSubmit={e=>{e.preventDefault();void create();}}>
          <label>Tên lô <input value={newName} onChange={e=>setNewName(e.target.value)} maxLength={120} required autoFocus/></label>
          <button className="primary" disabled={busy}>Tạo bản nháp</button><button type="button" onClick={()=>setShowNew(false)}>Hủy</button>
        </form>}
        <div className="page-title"><div><div className="eyebrow">META VIDEO ADS / {view==="dashboard"?"REPORTING":"WORKSPACE"}</div>
          <h1>{titles[view][0]}</h1><p>{titles[view][1]}</p></div>
          {view==="dashboard"?<button className="primary" onClick={()=>navigate("upload")}>+ Tải video</button>:
            view!=="settings"&&<div className="ad-count"><strong>{num}</strong> quảng cáo {batch?.published_at?"trong lô":"đủ điều kiện"}</div>}
        </div>
        {(error||notice)&&<div className={error?"alert danger":"alert good"} role={error?"alert":"status"}>{error||notice}</div>}
        {view==="dashboard"&&<Dashboard batches={workspace.batches} initialBatch={reportScope} openSource={openSource}
          load={api} goStart={()=>navigate("upload")} today={dayInZone()} />}
        {view==="settings"&&<div className="stack">
          <section className="card"><h2>Meta Marketing API</h2><p><span className="pill">Chưa kết nối</span> Chỉ tạo đối tượng demo. Không có token Meta trong ứng dụng.</p>
            <p>Để triển khai live cần một app Meta đã được cấp quyền, quyền trên tài khoản quảng cáo và các tài sản liên quan. Báo cáo cần <code>ads_read</code>; ghi chiến dịch cần <code>ads_management</code> và phê duyệt khi áp dụng.</p></section>
          <section className="card"><h2>Phân tích video</h2><p>{workspace.ai?"Gemini đã được cấu hình phía máy chủ, chỉ chạy khi bạn chủ động chọn.":"Chưa cấu hình Gemini. Phân tích và tạo copy dùng dữ liệu mô phỏng."}</p>
            <p>Video mẫu luôn dùng fixture. Với Gemini, chỉ video bạn chọn gửi mới rời thiết bị lưu trữ của máy chủ.</p></section>
          <section className="card"><h2>Vị trí dữ liệu</h2><p>Asia/Ho_Chi_Minh · VND. Số liệu và đối tượng demo nằm trong kho riêng, không đồng bộ với Meta.</p></section>
        </div>}
        {!batch && !["dashboard","settings"].includes(view)&&<div className="card">Đang tải bản nháp…</div>}
        {batch && view==="upload"&&<div className="stack">
          {batch.published_at ? <div className="card">Lô đã khóa. Chọn lô nháp khác hoặc tạo lô mới để tải thêm video.</div> : <>
            <section className="card">
              <div className={dragging?"drop dragging":"drop"} onDragOver={e=>{e.preventDefault();setDragging(true);}} onDragLeave={()=>setDragging(false)}
                onDrop={e=>{e.preventDefault();setDragging(false);addFiles(e.dataTransfer.files);}}>
                <span className="upload-mark" aria-hidden="true">↑</span><h2>Kéo và thả video vào đây</h2>
                <p>MP4, MOV hoặc WebM · tối đa {Math.round(workspace.bytesLimit/1024/1024)} MiB và {workspace.durationLimit} giây mỗi video.</p>
                <button className="primary" onClick={()=>uploader.current?.click()}>Chọn video</button>
                <input ref={uploader} hidden type="file" multiple accept=".mp4,.mov,.webm" onChange={e=>{if(e.target.files)addFiles(e.target.files);}}/>
              </div>
              {workspace.ai&&<label className="check"><input type="checkbox" checked={gemini} onChange={e=>setGemini(e.target.checked)}/>
                Gửi video tải lên tới Gemini để phân tích thật (tối đa 12 MiB / 60 giây; có thể phát sinh phí provider).</label>}
              <div className="sample-row"><div><strong>Chưa có video?</strong><p>Thử 3 video minh hoạ về sản phẩm chăm sóc tóc.</p></div>
                <button disabled={busy} onClick={()=>void save("samples",{keys:samples.map(s=>s.key),useDraftDefaults:true},"Đã thêm video mẫu và xếp hàng xử lý.")}>Thêm 3 video mẫu</button>
              </div>
            </section>
            <ContextForm key={batch.id} value={batch.context} save={v=>save("context",v,"Đã lưu bối cảnh chung.")} busy={busy} title="Bối cảnh chung của lô"/>
          </>}
          {transfers.some(t=>t.batch===batchId)&&<section className="card"><h2>Tiến độ tải lên</h2>
            {transfers.filter(t=>t.batch===batchId).map(t=><div className="upload-line" key={t.id}>
              <strong>{t.name}</strong><progress value={t.percent} max={100}/><span>{t.state==="waiting"?"Đang chờ":t.state==="done"?"Đã tải":t.state==="error"?t.error:t.percent+"%"}</span>
              {t.state==="error"&&<button onClick={()=>patchTransfer(t.id,{state:"waiting",error:undefined})}>Thử lại tải lên</button>}
            </div>)}
          </section>}
          <Creations key={batch.id} batch={batch} preview={preview} action={mutate} busy={busy} mode="queue"/>
          <div className="footer-actions"><button className="primary" onClick={()=>navigate("review")}>Duyệt & chỉnh sửa →</button></div>
        </div>}
        {batch&&(view==="review"||view==="library")&&<div className="stack">
          <Creations key={batch.id+view} batch={batch} preview={preview} action={mutate} busy={busy} mode={view} focus={focusVideo} revision={revision}/>
          {view==="review"&&<div className="footer-actions"><button className="primary" onClick={()=>navigate("campaign")}>Cài đặt chiến dịch →</button></div>}
        </div>}
        {batch&&view==="campaign"&&<Campaign key={batch.id} config={batch.config} locked={!!batch.published_at} busy={busy}
          save={(v:Config)=>save("config",v,"Đã lưu cấu hình chiến dịch.")} next={()=>navigate("publish")}/>}
        {batch&&view==="publish"&&<PublishPanel key={batch.id} batch={batch} preview={preview} busy={busy} action={mutate} report={()=>{
          setReportScope(batch.id);navigate("dashboard");
        }}/>}
      </main>
    </div>
  </div>;
}
