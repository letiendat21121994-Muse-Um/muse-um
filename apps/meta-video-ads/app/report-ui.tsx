"use client";
import { useEffect,useMemo,useState } from "react";
import { dayInZone } from "../lib/domain";
import { totals } from "../lib/metrics";
import type { Report,ReportRow } from "../lib/reporting";
import { integer,money } from "./studio";

type GroupBy="campaign"|"adset"|"ad"|"video"|"copy";
const dimensions:Record<GroupBy,string>={campaign:"Chiến dịch",adset:"Nhóm quảng cáo",ad:"Quảng cáo",video:"Video nguồn",copy:"Phiên bản copy"};
const pct=(value:number|null)=>value==null?"—":new Intl.NumberFormat("vi-VN",{maximumFractionDigits:2}).format(value)+"%";
const mult=(value:number|null)=>value==null?"—":new Intl.NumberFormat("vi-VN",{maximumFractionDigits:2}).format(value)+"×";
const sum=(rows:ReportRow[])=>totals(rows.map(r=>({...r.metrics,currency:r.currency})));
function groupRows(rows:ReportRow[], by:GroupBy) {
  const grouped=new Map<string,ReportRow[]>();
  for(const r of rows) {
    const key=by==="campaign"?r.campaign:by==="adset"?r.adSetId:by==="ad"?r.adId:by==="video"?r.videoId:r.variantId;
    if(!grouped.has(key))grouped.set(key,[]);
    grouped.get(key)!.push(r);
  }
  return [...grouped.entries()].map(([id,items])=>{
    const r=items[0];
    return {id,items,r,total:sum(items),name:by==="campaign"?r.campaignName:by==="adset"?r.groupName+" · "+r.campaignName:by==="ad"?r.adName:by==="video"?r.snapshot.filename:r.snapshot.copy.headline};
  });
}
type Loader=<T>(path:string,data?:unknown)=>Promise<T>;
export function Dashboard({batches,initialBatch,openSource,load,goStart,today}: {
  batches:{id:string;name:string;published_at:string|null}[];initialBatch:string;openSource:(batch:string,video:string)=>void;
  load:Loader;goStart:()=>void;today:string;
}) {
  const [from,setFrom]=useState(dayInZone(new Date(Date.now()-6*86400_000)));
  const [to,setTo]=useState(today);
  const [batch,setBatch]=useState(initialBatch);
  const [group,setGroup]=useState("");
  const [status,setStatus]=useState("");
  const [by,setBy]=useState<GroupBy>("campaign");
  const [data,setData]=useState<Report>();
  const [error,setError]=useState("");
  const [loading,setLoading]=useState(true);
  const [syncing,setSyncing]=useState(false);
  const [detail,setDetail]=useState("");
  const [campaignDrill,setCampaignDrill]=useState("");
  const [groupDrill,setGroupDrill]=useState("");
  const [page,setPage]=useState(0);
  const [chart,setChart]=useState<"spend"|"impressions"|"linkClicks">("spend");
  const qs=new URLSearchParams({from,to,batch,adSet:groupDrill,status}).toString();
  useEffect(()=>{
    let active=true;
    const reload=()=>load<Report>("report?"+qs).then(r=>{
      if(active){setData(r);setError("");setLoading(false);}
    }).catch(e=>{if(active){setError(e.message);setData(undefined);setLoading(false);}});
    setLoading(true);setPage(0);
    void reload();
    const timer=setInterval(reload,3600);
    return ()=>{active=false;clearInterval(timer);};
  },[load,qs]);
  const groups=useMemo(()=>new Map(data?.rows.map(r=>[r.adSetId,r.groupName+" · "+r.batchName])),[data]);
  const rows=useMemo(()=>data?.rows.filter(r=>!group||r.adSetId===group)??[],[data,group]);
  const shown=useMemo(()=>groupRows(rows,by),[rows,by]);
  const metrics=group?sum(rows):data?.totals;
  const csvQuery=new URLSearchParams(qs);
  if(group)csvQuery.set("adSet",group);
  const trend=group?[...new Set(rows.map(r=>r.day))].map(day=>({day,...sum(rows.filter(r=>r.day===day))})):data?.trend??[];
  const max=Math.max(1,...trend.map(a=>a[chart]??0));
  const chartLabel={spend:"Chi tiêu",impressions:"Lượt hiển thị",linkClicks:"Link clicks"}[chart];
  const go=(next:GroupBy)=>{setBy(next);setPage(0);setDetail("");};
  async function sync() {
    setSyncing(true);
    try {
      await load("report/refresh",{batch});
      const started=Date.now();
      while(Date.now()-started<6000) {
        await new Promise(resolve=>setTimeout(resolve,600));
        const result=await load<Report>("report?"+qs);
        if(result.lastSync && +new Date(result.lastSync)>=started-1500)break;
      }
      setError("");
    } catch(e){setError(e instanceof Error?e.message:"Không thể đồng bộ.");}finally{setSyncing(false);}
  }
  return <div className="stack">
    <div className="alert"><strong>Số liệu demo.</strong> Đây là kịch bản minh hoạ, không phải dữ liệu Ads Insights và không phải số tiền đã chi.</div>
    <section className="card">
      <div className="section-head"><div><h2>Bộ lọc báo cáo</h2>
        <p>{data?.zone||"Asia/Ho_Chi_Minh"} · {data?.currency||"VND"} · {data?.attribution||"Không có attribution thật."}</p></div>
        <div className="actions"><a className="button" href={"/api/report/csv?"+csvQuery.toString()} aria-disabled={!rows.length}
          onClick={e=>{if(!rows.length)e.preventDefault();}}>Xuất CSV</a><button disabled={syncing||!batches.some(b=>b.published_at)}
          onClick={()=>void sync()}>{syncing?"Đang đồng bộ…":"Làm mới số liệu"}</button></div>
      </div>
      <div className="filters">
        <label>Từ ngày <input type="date" value={from} onChange={e=>{if(e.target.value)setFrom(e.target.value);}}/></label>
        <label>Đến ngày <input type="date" value={to} onChange={e=>{if(e.target.value)setTo(e.target.value);}}/></label>
        <label>Tài khoản <select><option>Studio demo · VND</option></select></label>
        <label>Lô / chiến dịch <select value={batch} onChange={e=>{setBatch(e.target.value);setGroup("");setCampaignDrill("");setGroupDrill("");}}>
          <option value="">Tất cả lô đã xuất bản</option>{batches.filter(b=>b.published_at).map(b=><option key={b.id} value={b.id}>{b.name}</option>)}</select></label>
        <label>Nhóm quảng cáo <select value={group} onChange={e=>{setGroup(e.target.value);setPage(0);}}>
          <option value="">Tất cả nhóm</option>{[...groups].map(([g,label])=><option key={g} value={g}>{label}</option>)}</select></label>
        <label>Trạng thái <select value={status} onChange={e=>setStatus(e.target.value)}>
          <option value="">Tất cả trạng thái</option><option value="simulated">Mô phỏng thành công</option><option value="failed">Lỗi (không có số liệu)</option></select></label>
      </div>
      <small className="sync-note">Đồng bộ thành công gần nhất: {data?.lastSync?new Date(data.lastSync).toLocaleString("vi-VN",{timeZone:data.zone}):"Chưa có"}.</small>
    </section>
    {error&&<p className="alert danger" role="alert">{error}</p>}
    <div className="kpis">
      {[
        ["Chi tiêu",money(metrics?.spend),"Mô phỏng · VND"],
        ["Lượt hiển thị",integer(metrics?.impressions),"Impressions"],
        ["CTR",pct(metrics?.ctr??null),"Link clicks / impressions"],
        ["ROAS",mult(metrics?.roas??null),"Giá trị mua / chi tiêu"],
      ].map(([label,value,caption])=><section className="card kpi" key={label}><h2>{label}</h2><strong>{value}</strong><small>{caption}</small></section>)}
    </div>
    {loading&&!data?<div className="card">Đang tải báo cáo…</div>:!rows.length&&!error?
      <section className="card empty"><h2>Chưa có số liệu trong bộ lọc này</h2>
        <p>Xuất bản mô phỏng ít nhất một quảng cáo hoặc thử thay đổi khoảng ngày và bộ lọc.</p>
        <button className="primary" onClick={goStart}>Bắt đầu với video</button></section>:null}
    {!!rows.length&&<>
      <section className="card">
        <div className="section-head"><div><h2>Xu hướng theo ngày</h2><p>{chartLabel} · {trend.length} ngày có số liệu mẫu</p></div>
          <label>Chỉ số <select value={chart} onChange={e=>setChart(e.target.value as typeof chart)}>
            <option value="spend">Chi tiêu (VND)</option><option value="impressions">Lượt hiển thị</option><option value="linkClicks">Link clicks</option></select></label></div>
        <div className="chart" role="img" aria-label={chartLabel+" theo ngày: "+trend.map(r=>r.day+" "+(r[chart]??"không có")).join(", ")}>
          {trend.map(r=><div className="bar-slot" key={r.day} title={r.day+" · "+(chart==="spend"?money(r[chart]):integer(r[chart]))}>
            <div className="bar-track"><div style={{height:`${(r[chart]??0)/max*100}%`}}/></div><small>{r.day.slice(8)}/{r.day.slice(5,7)}</small>
          </div>)}
        </div>
      </section>
      <section className="card">
        <div className="section-head"><div><h2>Chi tiết hiệu suất</h2>
          <p>Chỉ số mua hàng dựa trên kịch bản Purchase. Dấu — nghĩa là không có hoặc không thể tổng hợp.</p></div>
          {(campaignDrill||groupDrill)&&<button onClick={()=>{setGroupDrill("");setCampaignDrill("");setBatch("");setGroup("");go("campaign");}}>← Tất cả chiến dịch</button>}
        </div>
        <div className="segmented" aria-label="Góc xem báo cáo">{Object.entries(dimensions).map(([k,label])=>
          <button key={k} className={by===k?"selected":""} onClick={()=>go(k as GroupBy)}>{label}</button>)}</div>
        <div className="table-wrap"><table><thead><tr><th>{dimensions[by]}</th><th>Chi tiêu</th><th>Hiển thị</th><th>Reach</th><th>Link clicks</th>
          <th>CTR</th><th>CPC</th><th>CPM</th><th>Purchase</th><th>CPA / Purchase</th><th>Giá trị mua</th><th>ROAS</th></tr></thead>
          <tbody>{shown.slice(page*12,page*12+12).map(({id,items,r,total:t,name})=><ReportLine key={id} {...{r,t,name,items,id,by}}
            detail={detail===id} open={()=>setDetail(detail===id?"":id)} source={openSource}
            drill={()=>{if(by==="campaign"){setBatch(r.batchId);setCampaignDrill(r.batchId);setGroupDrill("");setGroup("");go("adset");}
              else if(by==="adset"){setBatch(r.batchId);setGroupDrill(r.adSetId);setGroup("");go("ad");}
              else setDetail(detail===id?"":id);}}/>)}
          {metrics&&<tr className="total-row"><th scope="row">Tổng / tính lại</th><Cells t={metrics}/></tr>}</tbody></table></div>
        {shown.length>12&&<div className="footer-actions"><button disabled={!page} onClick={()=>setPage(page-1)}>Trước</button>
          <span>Trang {page+1} / {Math.ceil(shown.length/12)}</span><button disabled={(page+1)*12>=shown.length} onClick={()=>setPage(page+1)}>Sau</button></div>}
        <p className="muted">Reach không cộng giữa các quảng cáo hoặc các ngày. CTR = link clicks / impressions; CPC = chi tiêu / link clicks;
          CPM = chi tiêu / 1.000 impressions; CPA = chi tiêu / Purchase; ROAS = giá trị mua / chi tiêu. Chỉ số tỷ lệ tính từ các tổng.</p>
      </section>
    </>}
  </div>;
}
function Cells({t}:{t:ReturnType<typeof totals>}) {
  return <><td>{money(t.spend)}</td><td>{integer(t.impressions)}</td><td>{integer(t.reach)}</td><td>{integer(t.linkClicks)}</td>
    <td>{pct(t.ctr)}</td><td>{money(t.cpc)}</td><td>{money(t.cpm)}</td><td>{integer(t.purchases)}</td><td>{money(t.cpa)}</td><td>{money(t.value)}</td><td>{mult(t.roas)}</td></>;
}
function ReportLine({r,t,name,items,by,detail,open,drill,source}: {
  r:ReportRow;t:ReturnType<typeof totals>;name:string;items:ReportRow[];by:GroupBy;id:string;detail:boolean;open:()=>void;drill:()=>void;source:(batch:string,video:string)=>void;
}) {
  return <><tr><th scope="row"><button className="link" onClick={drill}>{name}</button>
    {["ad","video","copy"].includes(by)&&<small>{by==="ad"?r.adRef:by==="copy"?r.snapshot.filename:"Video gốc"}</small>}</th><Cells t={t}/></tr>
    {detail&&<tr><td colSpan={12}><div className="ad-detail">
      <video aria-label={r.snapshot.filename} controls preload="none" src={`/api/media/${r.videoId}`} poster={`/api/media/${r.videoId}/thumb`}/>
      <div><h3>{r.snapshot.filename}</h3><p>{new Set(items.map(i=>i.adId)).size} quảng cáo demo</p>
        {[...new Map(items.map(i=>[i.variantId,i.snapshot])).values()].map(a=><div key={a.variantId}><strong>{a.copy.headline}</strong>
          <p>{a.copy.primary}</p><p>{a.copy.description} · {a.copy.cta}</p></div>)}
        <p>{r.snapshot.destination} · {r.snapshot.tracking||"không có tracking"}</p>
        <button onClick={()=>source(r.batchId,r.videoId)}>Mở video / copy nguồn</button> <button onClick={open}>Thu gọn</button>
      </div></div></td></tr>}
  </>;
}
