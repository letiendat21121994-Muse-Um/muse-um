"use client";
import { useEffect,useState } from "react";
import { CURRENCY,ZONE,contextFields,ctas,objectives,type Config,type Context,type Copy } from "../lib/domain";
import type { BatchView,DraftVideo,Preview } from "../lib/workspace";

export type Action=(path:string,data:unknown,message?:string)=>Promise<boolean>;
export const money=(value:number|null|undefined,currency=CURRENCY)=>value==null?"—":
  new Intl.NumberFormat("vi-VN",{style:"currency",currency,maximumFractionDigits:0}).format(value);
export const integer=(value:number|null|undefined)=>value==null?"—":new Intl.NumberFormat("vi-VN",{maximumFractionDigits:0}).format(value);
const videoStages:Record<string,string>={uploading:"Đang tải",queued:"Trong hàng đợi",processing:"Đang phân tích",ready:"Sẵn sàng",failed:"Lỗi phân tích",upload_failed:"Lỗi tải lên"};
const adStages:Record<string,string>={queued:"Đang chờ",media:"Đang mô phỏng tải media",creative:"Đang mô phỏng tạo creative",
  ad:"Đang mô phỏng tạo quảng cáo",simulated:"Mô phỏng thành công",failed:"Lỗi demo"};
const fallback=(value:string|null|undefined)=>value||"Không có dữ liệu";
const fields:Record<keyof NonNullable<DraftVideo["analysis"]>["observations"],string>={
  product:"Sản phẩm / dịch vụ", sellingPoints:"Điểm nổi bật",speech:"Lời thoại",onScreen:"Chữ trên màn hình",problems:"Vấn đề",
  benefits:"Lợi ích được nhắc tới",offer:"Ưu đãi được nhắc tới",callToAction:"CTA trong video",hook:"Hook mở đầu",style:"Phong cách hình ảnh",
  audienceInference:"Đối tượng gợi ý (suy luận)",watchouts:"Điểm cần xác minh",
};

export function ContextForm({value,save,busy,title,disabled=false}: {value:Context;save:(v:Context)=>Promise<boolean>;busy:boolean;title:string;disabled?:boolean}) {
  const [draft,setDraft]=useState(value);
  return <form className="card" onSubmit={e=>{e.preventDefault();void save(draft);}}>
    <h2>{title}</h2><p>Thông tin bạn cung cấp, tách khỏi nội dung nhận thấy trong video.</p>
    <fieldset disabled={disabled||busy} className="fields">{Object.entries(contextFields).map(([k,label])=>{
      const key=k as keyof Context;
      const update=(v:string)=>setDraft(old=>({...old,[key]:v}));
      return <label key={key} className={key==="description"?"wide":""}>{label}
        {key==="description"?<textarea value={draft[key]??""} onChange={e=>update(e.target.value)} maxLength={2000}/>:
          <input type={key==="url"?"url":"text"} value={draft[key]??""} onChange={e=>update(e.target.value)} maxLength={key==="url"?2000:500}/>}</label>;
    })}
    {!disabled&&<div className="wide"><button type="submit">Lưu bối cảnh</button></div>}</fieldset>
  </form>;
}
const copyLabels:Record<keyof Omit<Copy,"cta">,string>={style:"Phong cách",primary:"Nội dung chính",headline:"Tiêu đề",description:"Mô tả",
  audience:"Đối tượng giả định",rationale:"Lý do gợi ý",improvements:"Gợi ý cải thiện video"};
function CopyCard({variant,index,busy,locked,action}: {variant:DraftVideo["variants"][number];index:number;busy:boolean;locked:boolean;action:Action}) {
  const [copy,setCopy]=useState(variant.data);
  const [dirty,setDirty]=useState(false);
  const path=`variants/${variant.id}/edit`;
  const change=(key:keyof Copy,value:string)=>{setCopy(old=>({...old,[key]:value}));setDirty(true);};
  return <form className="variant" onSubmit={async e=>{
    e.preventDefault();
    if(await action(path,{selected:!!variant.selected,copy},"Đã lưu copy."))setDirty(false);
  }}>
    <div className="section-head"><strong>V{index+1} · {copy.style}</strong>
      <label className="check"><input type="checkbox" disabled={busy||locked} checked={!!variant.selected}
        onChange={e=>void action(path,{selected:e.target.checked})}/>Chọn copy</label>
    </div>
    {variant.edited===1&&<small className="pill">Đã chỉnh tay</small>}
    <fieldset className="fields" disabled={locked||busy}>
      {Object.entries(copyLabels).filter(([key])=>key!=="style").map(([key,label])=>
        <label key={key} className={key==="primary"?"wide":""}>{label}
          {key==="primary"?<textarea required value={copy.primary} maxLength={2000} onChange={e=>change(key,e.target.value)}/>:
            <input value={copy[key as Exclude<keyof Copy,"cta">]} required onChange={e=>change(key as keyof Copy,e.target.value)}/>}</label>)}
      <label>CTA <select value={copy.cta} onChange={e=>change("cta",e.target.value)}>
        {Object.entries(ctas).map(([k,v])=><option value={k} key={k}>{v}</option>)}</select></label>
      {!locked&&<div className="wide"><button disabled={!dirty} type="submit">Lưu copy</button></div>}
    </fieldset>
  </form>;
}
function VideoDetail({video:v,batch,action,busy}: {video:DraftVideo;batch:BatchView;action:Action;busy:boolean}) {
  const path=`videos/${v.id}`;
  const locked=!!batch.published_at;
  const [values,setValues]=useState({included:!!v.included,destination:v.destination,tracking:v.tracking,group_key:v.group_key});
  useEffect(()=>setValues({included:!!v.included,destination:v.destination,tracking:v.tracking,group_key:v.group_key}),[v.included,v.destination,v.tracking,v.group_key]);
  const a=v.analysis;
  return <div className="detail">
    <div className="detail-top">
      {v.status!=="upload_failed"&&v.status!=="uploading"&&<video aria-label={v.filename} src={`/api/media/${v.id}`} controls preload="none"
        poster={v.thumb?`/api/media/${v.id}/thumb`:undefined}/>}
      <div>
        <h3>{v.filename}</h3>
        {v.error&&<p className="alert danger">{v.error}</p>}
        {a?<><p className="pill">{a.attribution}</p><h4>{a.provider==="fixture"?"Quan sát minh hoạ (không phải kết quả AI)":"Quan sát từ video"}</h4>
          <dl className="analysis">{Object.entries(fields).filter(([k])=>k!=="audienceInference"&&k!=="watchouts").map(([k,label])=>
            <div key={k}><dt>{label}</dt><dd>{fallback(a.observations[k as keyof typeof a.observations])}</dd></div>)}</dl>
          <h4>Suy luận và kiểm tra</h4><p>{fallback(a.observations.audienceInference)}</p><p>{fallback(a.observations.watchouts)}</p>
          <details><summary>Bối cảnh đã dùng gần nhất</summary>
            <dl className="analysis">{Object.entries(a.provided).filter(([,v])=>v).map(([k,v])=><div key={k}><dt>{contextFields[k as keyof Context]}</dt><dd>{v}</dd></div>)}</dl></details>
        </>:<p>Chưa có phân tích. Khi xử lý xong, copy sẽ xuất hiện tại đây.</p>}
      </div>
    </div>
    <form className="card" onSubmit={e=>{e.preventDefault();void action(path+"/edit",{...values,overrides:v.overrides},"Đã lưu thiết lập video.");}}>
      <h3>Thiết lập riêng cho video</h3>
      <fieldset disabled={busy||locked} className="fields">
        <label className="check"><input type="checkbox" checked={values.included} onChange={e=>setValues({...values,included:e.target.checked})}/>Đưa video vào chiến dịch</label>
        <label>Nhóm quảng cáo <select disabled={batch.config.structure==="shared"} value={batch.config.structure==="shared"?"shared":values.group_key}
          onChange={e=>setValues({...values,group_key:e.target.value})}>
          {!batch.config.groups.some(g=>g.key===values.group_key)&&<option value={values.group_key}>Chưa được gán</option>}
          {batch.config.groups.map(g=><option key={g.key} value={g.key}>{g.name}</option>)}</select></label>
        <label>URL đích <input type="url" placeholder="Dùng cấu hình chung nếu để trống" value={values.destination} onChange={e=>setValues({...values,destination:e.target.value})}/></label>
        <label>Tracking <input placeholder="Dùng cấu hình chung nếu để trống" value={values.tracking} onChange={e=>setValues({...values,tracking:e.target.value})}/></label>
        {!locked&&<div className="wide"><button type="submit">Lưu thiết lập video</button></div>}
      </fieldset>
    </form>
    <ContextForm value={v.overrides} save={overrides=>action(path+"/edit",{included:!!v.included,destination:v.destination,tracking:v.tracking,
      group_key:v.group_key,overrides},"Đã lưu bối cảnh riêng. Tạo lại copy để áp dụng.")} busy={busy} disabled={locked} title="Bối cảnh riêng (tuỳ chọn)"/>
    {v.variants.length>0&&<section>
      <div className="section-head"><div><h3>Phiên bản copy</h3><p>So sánh, chỉnh sửa và chọn các phiên bản muốn dùng.</p></div>
        {!locked&&<div className="actions">
          <button disabled={busy||v.regenerating} onClick={()=>void action(path+"/regenerate",{},"Đã xếp hàng tạo lại copy.")}>
            {v.regenerating?"Đang tạo…":"Tạo lại, giữ bản chỉnh tay"}</button>
          <button disabled={busy||v.regenerating} onClick={()=>{
            if(window.confirm("Thay thế cả các bản copy đã chỉnh tay?"))void action(path+"/regenerate",{replace:true});
          }}>Thay thế tất cả copy</button>
        </div>}
      </div>
      {v.regenerationError&&<p className="alert danger">{v.regenerationError}</p>}
      <div className="variant-grid">{v.variants.map((c,index)=><CopyCard key={c.id} variant={c} index={index} busy={busy} locked={locked} action={action}/>)}</div>
    </section>}
  </div>;
}
export function Creations({batch,preview,action,busy,mode,focus}: {batch:BatchView;preview?:Preview;action:Action;busy:boolean;mode:"queue"|"library"|"review";focus?:string}) {
  const [search,setSearch]=useState("");
  const [status,setStatus]=useState("");
  const [open,setOpen]=useState("");
  const [checked,setChecked]=useState<string[]>([]);
  const [url,setUrl]=useState("");
  const [tracking,setTracking]=useState("");
  const [group,setGroup]=useState("");
  const [overwrite,setOverwrite]=useState(false);
  const [allBatches,setAllBatches]=useState(false);
  const [external,setExternal]=useState<BatchView[]>([]);
  const [libraryError,setLibraryError]=useState("");
  const locked=!!batch.published_at;
  useEffect(()=>{if(focus){setOpen(focus);setSearch("");setStatus("");}},[focus]);
  useEffect(()=>{
    if(!allBatches)return;
    let current=true;
    fetch("/api/workspace").then(r=>r.json()).then(async (w:{batches?:{id:string}[]})=>{
      if(!w.batches)throw Error();
      const list=await Promise.all(w.batches.filter(b=>b.id!==batch.id).map(async b=>{
        const response=await fetch("/api/batches/"+b.id);
        if(!response.ok)throw Error();
        return await response.json() as BatchView;
      }));
      if(current){setExternal(list);setLibraryError("");}
    }).catch(()=>{if(current)setLibraryError("Không tải được các lô khác.");});
    return ()=>{current=false;};
  },[allBatches,batch.id]);
  const list=[batch,...(mode==="library"&&allBatches?external:[])].flatMap(source=>source.videos.map(v=>({v,source})))
    .filter(({v})=>v.filename.toLocaleLowerCase("vi").includes(search.toLocaleLowerCase("vi"))&&(!status||v.status===status));
  const bulk=(all:boolean)=>{
    if(!url&&!tracking&&!group)return;
    if(overwrite&&!window.confirm("Ghi đè giá trị riêng của các dòng được áp dụng?"))return;
    void action(`batches/${batch.id}/bulk`,{all,ids:checked,overwrite,destination:url||undefined,tracking:tracking||undefined,group:group||undefined},
      overwrite?"Đã áp dụng và ghi đè giá trị riêng.":"Đã áp dụng ở nơi chưa có giá trị riêng.");
  };
  return <section className="card creations">
    <div className="section-head"><div><h2>{mode==="queue"?"Hàng đợi video":mode==="review"?"Danh sách duyệt":"Video đã lưu"}</h2>
      <p>{mode==="review"?"Mở một dòng để xem phân tích, chỉnh copy hoặc chỉnh URL và tracking.":`${batch.videos.length} video trong lô hiện tại.`}</p></div>
      <div className="actions">
        {mode==="library"&&<label className="check"><input type="checkbox" checked={allBatches} onChange={e=>setAllBatches(e.target.checked)}/>Tất cả lô</label>}
        <input aria-label="Tìm theo tên video" type="search" placeholder="Tìm theo tên video" value={search} onChange={e=>setSearch(e.target.value)}/>
        <select aria-label="Lọc trạng thái video" value={status} onChange={e=>setStatus(e.target.value)}>
          <option value="">Mọi trạng thái</option>{Object.entries(videoStages).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>
      </div>
    </div>
    {libraryError&&<p className="alert danger">{libraryError}</p>}
    {mode==="review"&&!locked&&batch.videos.length>0&&<div className="bulk">
      <strong>Áp dụng cài đặt hàng loạt</strong>
      <div className="fields">
        <label>URL đích <input type="url" value={url} onChange={e=>setUrl(e.target.value)} placeholder="https://…"/></label>
        <label>Tracking <input value={tracking} onChange={e=>setTracking(e.target.value)} placeholder="utm_campaign=…"/></label>
        {batch.config.structure==="grouped"&&<label>Nhóm <select value={group} onChange={e=>setGroup(e.target.value)}>
          <option value="">Không đổi</option>{batch.config.groups.map(g=><option key={g.key} value={g.key}>{g.name}</option>)}</select></label>}
      </div>
      <div className="actions"><label className="check"><input type="checkbox" checked={overwrite} onChange={e=>setOverwrite(e.target.checked)}/>
        Cho phép ghi đè giá trị riêng</label>
        <button disabled={busy||!checked.length||(!url&&!tracking&&!group)} onClick={()=>bulk(false)}>Áp dụng cho dòng đã chọn ({checked.length})</button>
        <button disabled={busy||(!url&&!tracking&&!group)} onClick={()=>bulk(true)}>Áp dụng cho tất cả</button></div>
    </div>}
    {list.length===0?<div className="empty">{batch.videos.length?"Không tìm thấy video phù hợp.":"Chưa có video. Tải lên hoặc thêm video mẫu để bắt đầu."}</div>:
      <div className="table-wrap"><table className="creative-table">
        <thead><tr>{mode==="review"&&!locked&&<th scope="col">Chọn dòng</th>}<th scope="col">Video</th><th scope="col">Xử lý</th>
          <th scope="col">Copy đã chọn</th><th scope="col">Nhóm / kiểm tra</th><th scope="col">Tác vụ</th></tr></thead>
        <tbody>{list.map(({v,source})=>{
          const sourceLocked=!!source.published_at;
          const adList=source.ads.filter(a=>a.video_id===v.id);
          const issue=source.id===batch.id?preview?.excluded.find(x=>x.videoId===v.id):undefined;
          return <VideoRow key={v.id} {...{v,source,issue,adList,sourceLocked,action,busy,mode}} selected={checked.includes(v.id)} toggleCheck={value=>
            setChecked(old=>value?[...old,v.id]:old.filter(x=>x!==v.id))} expanded={open===v.id} toggle={()=>setOpen(open===v.id?"":v.id)} />;
        })}</tbody>
      </table></div>}
  </section>;
}
function VideoRow({v,source,issue,adList,sourceLocked,action,busy,mode,selected,toggleCheck,expanded,toggle}: {
  v:DraftVideo;source:BatchView;issue?:Preview["excluded"][number];adList:BatchView["ads"];sourceLocked:boolean;action:Action;busy:boolean;
  mode:"queue"|"library"|"review";selected:boolean;toggleCheck:(value:boolean)=>void;expanded:boolean;toggle:()=>void;
}) {
  return <><tr id={"video-"+v.id}>
    {mode==="review"&&!sourceLocked&&<td><input type="checkbox" checked={selected} aria-label={"Chọn dòng "+v.filename} onChange={e=>toggleCheck(e.target.checked)}/></td>}
    <td><div className="media-cell">{v.thumb?<img alt="" src={`/api/media/${v.id}/thumb`} loading="lazy"/>:<span className="placeholder">▱</span>}
      <span><button className="link" onClick={toggle} aria-expanded={expanded}>{v.filename}</button>
        <small>{mode==="library"?source.name+" · ":""}{v.duration?`${Math.ceil(v.duration)} giây`:"Chưa đọc thời lượng"}</small></span></div></td>
    <td><span className={"pill "+(v.status==="ready"?"ok":v.status.includes("failed")?"bad":"")}>{videoStages[v.status]||v.status}</span></td>
    <td>{v.variants.filter(c=>c.selected).length} / {v.variants.length}{!v.included&&<small>Đã loại khỏi lô</small>}</td>
    <td>{source.config.structure==="shared"?"Nhóm chung":source.config.groups.find(g=>g.key===v.group_key)?.name||"Chưa gán nhóm"}
      {issue&&<small>{issue.reason}</small>}{adList.length>0&&<small>{adList.map(a=>adStages[a.state]).join(" · ")}</small>}
      {v.error&&<small className="danger-text">{v.error}</small>}</td>
    <td><div className="actions">{!sourceLocked&&v.status==="failed"&&<button disabled={busy} onClick={()=>void action(`videos/${v.id}/retry`,{})}>Thử lại xử lý</button>}
      {!sourceLocked&&!["processing","uploading"].includes(v.status)&&<button disabled={busy} onClick={()=>{
        if(window.confirm("Xóa video và copy khỏi bản nháp?"))void action(`videos/${v.id}/remove`,{},"Đã xóa video.");
      }}>Xóa</button>}
      <button onClick={toggle} aria-label={expanded?"Thu gọn "+v.filename:"Mở "+v.filename}>{expanded?"Thu gọn":"Chi tiết"}</button></div></td>
  </tr>
  {expanded&&<tr><td colSpan={mode==="review"&&!sourceLocked?6:5}><VideoDetail video={v} batch={source} action={action} busy={busy}/></td></tr>}</>;
}

export function Campaign({config,locked,busy,save,next}: {config:Config;locked:boolean;busy:boolean;save:(v:Config)=>Promise<boolean>;next:()=>void}) {
  const [draft,setDraft]=useState(config);
  const [dirty,setDirty]=useState(false);
  const [step,setStep]=useState(0);
  useEffect(()=>{ if(!dirty)setDraft(config); },[config,dirty]);
  function change<K extends keyof Config>(k:K,value:Config[K]) {setDraft(old=>({...old,[k]:value}));setDirty(true);}
  function changeGroup(i:number,values:Partial<Config["groups"][number]>) {change("groups",draft.groups.map((g,j)=>i===j?{...g,...values}:g));}
  const names=["1. Chiến dịch","2. Nhóm & đối tượng","3. Quảng cáo"];
  const structure=(value:Config["structure"])=>{
    setDraft(old=>{
      const first=old.groups[0];
      return {...old,structure:value,groups:value==="shared"?[{...first,key:"shared"}]:old.groups.length>=2?old.groups:
        [{...first,key:"shared"},{...first,key:"audience-2",name:"Nhóm thứ hai"}]};
    });setDirty(true);
  };
  return <form className="stack" onSubmit={async e=>{e.preventDefault();if(await save(draft))setDirty(false);}}>
    <div className="segmented">{names.map((label,i)=><button type="button" className={step===i?"selected":""} onClick={()=>setStep(i)} key={label}>{label}</button>)}</div>
    <section className="card">
      <h2>{names[step].replace(/^\d\. /,"")}</h2>
      {locked&&<p className="alert">Cấu hình đã khóa khi xuất bản demo.</p>}
      <fieldset disabled={locked||busy} className="fields">
        {step===0&&<>
          <label>Tên chiến dịch <input value={draft.name} required maxLength={120} onChange={e=>change("name",e.target.value)}/></label>
          <label>Tài khoản quảng cáo <select defaultValue="demo-vn"><option value="demo-vn">Studio demo · VND · Việt Nam</option></select></label>
          <label>Mục tiêu <select value={draft.objective} onChange={e=>change("objective",e.target.value as Config["objective"])}>
            {Object.entries(objectives).map(([k,v])=><option value={k} key={k}>{v}</option>)}</select></label>
          <label>Tối ưu hóa <select value={draft.optimization} onChange={e=>change("optimization",e.target.value as Config["optimization"])}>
            <option value="PURCHASE">Purchase</option><option value="LINK_CLICKS">Link clicks</option></select></label>
          <label>Dataset / Pixel (demo) <input value={draft.dataset} maxLength={100} onChange={e=>change("dataset",e.target.value)}/></label>
          <label>Cấp đặt ngân sách <select value={draft.scope} onChange={e=>change("scope",e.target.value as Config["scope"])}>
            <option value="campaign">Chiến dịch</option><option value="adset">Nhóm quảng cáo</option></select></label>
          <label>Loại ngân sách <select value={draft.budgetType} onChange={e=>change("budgetType",e.target.value as Config["budgetType"])}>
            <option value="daily">Hằng ngày</option><option value="lifetime">Trọn đời</option></select></label>
          {draft.scope==="campaign"&&<label>Ngân sách chiến dịch (VND) <input type="number" min={1000} max={10000000000} required value={draft.budget} onChange={e=>change("budget",Number(e.target.value))}/></label>}
          <label>Giới hạn chi tiêu (VND) <input type="number" min={0} max={10000000000} value={draft.spendCap} onChange={e=>change("spendCap",Number(e.target.value))}/><small>0 = không đặt giới hạn.</small></label>
          <label>Bắt đầu ({ZONE}) <input type="datetime-local" required value={draft.start} onChange={e=>change("start",e.target.value)}/></label>
          <label>Kết thúc ({ZONE}) <input type="datetime-local" required={draft.budgetType==="lifetime"} value={draft.end} onChange={e=>change("end",e.target.value)}/></label>
        </>}
        {step===1&&<>
          <label className="wide">Cấu trúc nhóm <select value={draft.structure} onChange={e=>structure(e.target.value as Config["structure"])}>
            <option value="shared">Một chiến dịch · một nhóm chung</option><option value="grouped">Một chiến dịch · nhiều nhóm theo đối tượng</option></select></label>
          {draft.groups.map((g,i)=><div className="wide group fields" key={i}>
            <div className="wide section-head"><strong>Nhóm {i+1}</strong>{draft.structure==="grouped"&&i>0&&
              <button type="button" onClick={()=>change("groups",draft.groups.filter((_,j)=>j!==i))}>Xóa nhóm</button>}</div>
            <label>Tên nhóm <input value={g.name} required maxLength={100} onChange={e=>changeGroup(i,{name:e.target.value})}/></label>
            <label>Mã quốc gia <input value={g.country} required pattern="[A-Z]{2}" maxLength={2} onChange={e=>changeGroup(i,{country:e.target.value.toUpperCase()})}/></label>
            <label>Tuổi tối thiểu <input type="number" min={18} max={65} value={g.agesMin} onChange={e=>changeGroup(i,{agesMin:Number(e.target.value)})}/></label>
            <label>Tuổi tối đa <input type="number" min={18} max={65} value={g.agesMax} onChange={e=>changeGroup(i,{agesMax:Number(e.target.value)})}/></label>
            <label>Sở thích (nhãn demo) <input value={g.interests} onChange={e=>changeGroup(i,{interests:e.target.value})}/></label>
            <label>Vị trí hiển thị <select value={g.placements} onChange={e=>changeGroup(i,{placements:e.target.value as typeof g.placements})}>
              <option value="automatic">Tự động</option><option value="feeds">Feeds</option><option value="reels">Reels</option></select></label>
            {draft.scope==="adset"&&<label>Ngân sách nhóm (VND) <input type="number" min={1000} max={10000000000} value={g.budget} onChange={e=>changeGroup(i,{budget:Number(e.target.value)})}/></label>}
          </div>)}
          {draft.structure==="grouped"&&draft.groups.length<8&&<button type="button" onClick={()=>change("groups",[
            ...draft.groups,{...draft.groups[0],key:"aud-"+crypto.randomUUID().slice(0,8),name:`Nhóm ${draft.groups.length+1}`},
          ])}>+ Thêm nhóm</button>}
          <p className="wide">Chỉ các nhóm có quảng cáo đủ điều kiện mới được tạo. Gán video vào nhóm trong màn hình duyệt.</p>
        </>}
        {step===2&&<>
          <label>Facebook Page mô phỏng <input value={draft.page} required maxLength={100} onChange={e=>change("page",e.target.value)}/></label>
          <label>Instagram (tuỳ chọn) <input value={draft.instagram} maxLength={100} onChange={e=>change("instagram",e.target.value)}/></label>
          <label>Tiền tố tên quảng cáo <input value={draft.namePrefix} required maxLength={50} onChange={e=>change("namePrefix",e.target.value)}/></label>
          <label>URL đích mặc định <input type="url" value={draft.destination} onChange={e=>change("destination",e.target.value)}/></label>
          <label>Tracking mặc định <input value={draft.tracking} maxLength={500} onChange={e=>change("tracking",e.target.value)}/></label>
        </>}
      </fieldset>
    </section>
    <div className="footer-actions">
      {dirty&&<small>Thay đổi chưa lưu</small>}
      {!locked&&<button className="primary" disabled={busy||!dirty} type="submit">Lưu cấu hình bản nháp</button>}
      <button disabled={dirty} type="button" onClick={next}>Xem lại trước khi xuất bản →</button>
    </div>
  </form>;
}
export function PublishPanel({batch,preview,busy,action,report}: {batch:BatchView;preview?:Preview;busy:boolean;action:Action;report:()=>void}) {
  const [fail,setFail]=useState(false);
  const cfg=batch.config;
  const path=`batches/${batch.id}`;
  const successes=batch.ads.filter(a=>a.state==="simulated").length;
  const failures=batch.ads.filter(a=>a.state==="failed").length;
  const remaining=batch.ads.length-successes-failures;
  return <div className="stack">
    <div className="alert"><strong>Chỉ mô phỏng.</strong> Không gửi Meta, không xét duyệt, không phân phối và không trừ ngân sách.</div>
    <section className="card"><h2>{cfg.name}</h2><p>{objectives[cfg.objective]} · {cfg.optimization} · {cfg.page}{cfg.instagram?" · "+cfg.instagram:""}</p>
      <dl className="analysis"><div><dt>Ngân sách cấu hình</dt><dd>{money(preview?.budget)} {cfg.budgetType==="daily"?"/ ngày":"trọn đời"} · cấp {cfg.scope==="campaign"?"chiến dịch":"nhóm"}</dd></div>
        <div><dt>Lịch ({ZONE})</dt><dd>{cfg.start.replace("T"," ")} → {cfg.end.replace("T"," ")||"Chưa đặt kết thúc"}</dd></div>
        <div><dt>Giới hạn chi tiêu</dt><dd>{cfg.spendCap?money(cfg.spendCap):"Không đặt"}</dd></div>
        <div><dt>Phân bổ</dt><dd>Không nhân ngân sách theo số video hoặc chia đều cho mỗi quảng cáo.</dd></div>
      </dl>
      <h3>Nhóm sẽ dùng</h3>
      {cfg.groups.filter(g=>preview?.groups.includes(g.key)).map(g=><p key={g.key}><strong>{g.name}</strong> · {g.country} · {g.agesMin}–{g.agesMax} tuổi
        {" · "+({automatic:"Tự động",feeds:"Feeds",reels:"Reels"}[g.placements])}{g.interests?" · "+g.interests:""}{cfg.scope==="adset"?" · "+money(g.budget):""}</p>)}
      {batch.campaign_ref&&<p><code>{batch.campaign_ref}</code></p>}
    </section>
    {!batch.published_at?<section className="card">
      <h2>Quảng cáo đã chọn ({preview?.plan.length??0})</h2>
      {!!preview?.issues.length&&<div className="alert danger" role="alert">{preview.issues.map(i=><p key={i}>{i}</p>)}</div>}
      {!!preview?.plan.length&&<div className="table-wrap"><table><thead><tr><th>Quảng cáo / video</th><th>Copy</th><th>Nhóm</th><th>Đích / tracking</th></tr></thead>
        <tbody>{preview.plan.map(ad=><tr key={ad.variantId}>
          <td>{ad.name}<small>{ad.filename}</small></td><td>{ad.copy.headline}<small>{ad.copy.primary}</small></td>
          <td>{cfg.groups.find(g=>g.key===ad.group)?.name||ad.group}</td><td>{ad.destination}<small>{ad.tracking||"Không có tracking"}</small></td></tr>)}</tbody></table></div>}
      {!!preview?.excluded.length&&<details className="excluded"><summary>Video bị loại ({preview.excluded.length})</summary>
        {preview.excluded.map((v,i)=><p key={i}>{v.filename} · {v.reason}</p>)}</details>}
      <div className="footer-actions"><label className="check"><input type="checkbox" checked={fail} onChange={e=>setFail(e.target.checked)}/>Mô phỏng một lỗi để thử phục hồi</label>
        <button className="primary" disabled={busy||!preview||!!preview.issues.length} onClick={()=>{
          if(window.confirm(`Xuất bản mô phỏng ${preview?.plan.length??0} quảng cáo? Không có dữ liệu gửi đến Meta.`))
            void action(path+"/publish",{confirm:true,simulateFailure:fail,reviewKey:preview?.reviewKey},"Đã đưa quảng cáo vào hàng đợi demo.");
        }}>Xuất bản quảng cáo đã chọn (demo)</button></div>
    </section>:<section className="card">
      <div className="section-head"><div><h2>Kết quả xuất bản demo</h2>
        <p aria-live="polite">{successes} thành công mô phỏng · {failures} lỗi · {remaining} chờ hoặc đang xử lý</p></div>
        <div className="actions">{failures>0&&<button disabled={busy} onClick={()=>void action(path+"/retry",{},"Chỉ xếp hàng lại các quảng cáo lỗi.")}>Thử lại quảng cáo lỗi</button>}
          {successes>0&&<button className="primary" onClick={report}>Xem báo cáo demo →</button>}</div></div>
      <div className="table-wrap"><table><thead><tr><th>Quảng cáo / nguồn</th><th>Trạng thái</th><th>ID mô phỏng</th></tr></thead>
        <tbody>{batch.ads.map(ad=><tr key={ad.id}><td>{ad.name}<small>{ad.snapshot.filename} · {ad.snapshot.copy.headline}</small></td>
          <td><span className={"pill "+(ad.state==="failed"?"bad":ad.state==="simulated"?"ok":"")}>{adStages[ad.state]}</span>{ad.error&&<small>{ad.error}</small>}</td>
          <td><small>{ad.ad_ref??ad.creative_ref??ad.media_ref??"Chưa có"}</small></td></tr>)}</tbody></table></div>
    </section>}
    {batch.activity.length>0&&<section className="card"><h2>Nhật ký hoạt động</h2>{batch.activity.map((a,i)=>
      <div className="log" key={i}><time>{new Date(a.created_at).toLocaleString("vi-VN",{timeZone:ZONE})}</time><span>{a.message}</span></div>)}</section>}
  </div>;
}
