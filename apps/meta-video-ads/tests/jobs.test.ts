import assert from "node:assert/strict";
import { mkdtempSync,rmSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { test } from "node:test";
import { Db } from "../lib/db.ts";
import { tick,retryPublish } from "../lib/jobs.ts";
import { defaultConfig,dayInZone } from "../lib/domain.ts";
import { report,reportCsv } from "../lib/reporting.ts";
import { inspect } from "../lib/media.ts";
import { addVideo,addVideos,batchView,createBatch,ownedBatch,ownedVideo,preview,publish,newSession,session, type Ad } from "../lib/workspace.ts";

test("WebM with no container duration is measured through the last frame",async()=>{
  const root=mkdtempSync(join(tmpdir(),"meta-webm-test-"));
  const oldLimit=process.env.MAX_VIDEO_SECONDS;
  try {
    const movie=join(root,"live.webm"), thumb=join(root,"thumb.jpg");
    execFileSync("ffmpeg",["-loglevel","error","-i","public/samples/ritual.mp4","-vf","fps=3/2","-frames:v","2",
      "-an","-c:v","libvpx-vp9","-live","1",movie]);
    const duration=execFileSync("ffprobe",["-v","error","-show_entries","format=duration","-of","csv=p=0",movie],{encoding:"utf8"});
    assert.equal(duration.trim(),"N/A");
    process.env.MAX_VIDEO_SECONDS="2";
    const measured=await inspect(movie,"video/webm",thumb);
    assert.ok(measured>1.3&&measured<1.4);
    process.env.MAX_VIDEO_SECONDS="1";
    await assert.rejects(inspect(movie,"video/webm",thumb),/dài hơn 1 giây/);

    const opus=join(root,"with-opus.webm");
    execFileSync("ffmpeg",["-loglevel","error","-f","lavfi","-i","color=c=blue:s=160x160:r=25:d=1",
      "-f","lavfi","-i","sine=d=1","-c:v","libvpx-vp9","-c:a","libopus","-live","1",opus]);
    const measuredOpus=await inspect(opus,"video/webm",thumb);
    assert.ok(Math.abs(measuredOpus-1)<0.001);
  } finally {
    if (oldLimit===undefined) delete process.env.MAX_VIDEO_SECONDS;
    else process.env.MAX_VIDEO_SECONDS=oldLimit;
    rmSync(root,{force:true,recursive:true});
  }
});

test("adding sample videos respects capacity atomically",()=>{
  const root=mkdtempSync(join(tmpdir(),"meta-sample-capacity-")), db=new Db(root);
  try {
    const owner=session(db,newSession(db))!;
    const batch=ownedBatch(db,owner,createBatch(db,owner,"Capacity"));
    const input={filename:"ritual.mp4",mime:"video/mp4",sample:"ritual"};
    assert.equal(addVideos(db,batch,Array.from({length:23},()=>input)).length,23);
    assert.throws(()=>addVideos(db,batch,[input,input,input]),/tối đa 24/);
    assert.equal(batchView(db,owner,batch.id).videos.length,23);
  } finally {db.sql.close();rmSync(root,{force:true,recursive:true});}
});

test("independent analysis, preserved copy, confirmed mapping and part-way publish recovery",async()=>{
  const root=mkdtempSync(join(tmpdir(),"meta-video-test-"));
  let db=new Db(root);
  try {
    const token=newSession(db), owner=session(db,token)!, other=session(db,newSession(db))!;
    assert.notEqual(owner,other);
    assert.equal(session(db,token+"oops"),undefined);
    const id=createBatch(db,owner,"Thử batch");
    const batch=ownedBatch(db,owner,id);
    const ritual=addVideo(db,batch,{filename:"=Ritual.mp4",mime:"video/mp4",sample:"ritual"});
    const texture=addVideo(db,batch,{filename:"texture.mp4",mime:"video/mp4",sample:"texture"});
    assert.throws(()=>ownedBatch(db,other,id),/Không tìm thấy/);
    assert.throws(()=>ownedVideo(db,other,ritual),/Không tìm thấy/);
    const cfg={...defaultConfig(),destination:"https://example.com/page"};
    db.run("UPDATE batch SET config=? WHERE id=?",JSON.stringify(cfg),id);
    while(await tick(db,0)) {}
    const views=batchView(db,owner,id).videos;
    assert.deepEqual(views.map(v=>v.status),["ready","ready"]);
    assert.notEqual(views[0].analysis?.observations.onScreen,views[1].analysis?.observations.onScreen);
    assert.equal(views[0].variants.length,3);
    assert.notEqual(views[0].variants[0].data.primary,views[1].variants[0].data.primary);
    const selected=views[0].variants[0];
    const stale=preview(db,ownedBatch(db,owner,id));
    db.run("UPDATE variant SET edited=1,data=? WHERE id=?",JSON.stringify({...selected.data,headline:"Copy riêng"}),selected.id);
    assert.throws(()=>publish(db,owner,id,true,stale.reviewKey),/đã thay đổi/);
    db.run("UPDATE variant SET selected=1 WHERE id=?",views[0].variants[1].id);
    const plan=preview(db,ownedBatch(db,owner,id));
    assert.equal(plan.plan.length,3);
    assert.equal(plan.budget,cfg.budget);
    assert.equal(plan.plan.filter(a=>a.videoId===ritual).length,2);
    assert.equal(plan.plan.find(a=>a.variantId===selected.id)?.copy.headline,"Copy riêng");
    const camp=publish(db,owner,id,true,plan.reviewKey);
    assert.match(camp!,/^demo_campaign_/);
    assert.equal(publish(db,owner,id,false,stale.reviewKey),camp);
    assert.throws(()=>publish(db,other,id,false,stale.reviewKey),/Không tìm thấy/);
    await tick(db,0);
    const before=db.one<Ad>("SELECT * FROM ad WHERE state='failed'")!;
    assert.equal(before.video_id,ritual);
    assert.match(before.media_ref!,/^demo_media_/);
    assert.match(before.creative_ref!,/^demo_creative_/);
    assert.equal(before.ad_ref,null);
    while(await tick(db,0)) {}
    assert.equal(db.all("SELECT * FROM ad WHERE state='simulated'").length,2);
    const successBefore=db.all("SELECT id,ad_ref FROM ad WHERE state='simulated' ORDER BY id");
    db.sql.close();
    db=new Db(root);
    assert.equal(retryPublish(db,id),1);
    assert.equal(retryPublish(db,id),0);
    const successAfterRetryQueued=db.all("SELECT id,ad_ref FROM ad WHERE state='simulated' ORDER BY id");
    assert.deepEqual(successAfterRetryQueued,successBefore);
    while(await tick(db,0)) {}
    const ads=batchView(db,owner,id).ads;
    assert.ok(ads.some(a=>a.video_id===texture));
    assert.deepEqual(ads.map(a=>a.state),["simulated","simulated","simulated"]);
    const after=ads.find(a=>a.id===before.id)!;
    assert.equal(after.media_ref,before.media_ref);
    assert.equal(after.creative_ref,before.creative_ref);
    assert.equal(ads[0].media_ref,ads[1].media_ref);
    assert.equal(db.all("SELECT * FROM media_mapping").length,2);
    assert.equal(db.all("SELECT * FROM ad_set").length,1);
    assert.equal(db.all("SELECT * FROM batch WHERE campaign_ref IS NOT NULL").length,1);
    const filters={from:"2020-01-01",to:dayInZone(),batch:id,status:"",adSet:""};
    const result=report(db,owner,filters);
    assert.equal(result.rows.length,21);
    assert.equal(result.totals.reach,null);
    assert.equal(report(db,other,filters).rows.length,0);
    assert.equal(report(db,owner,{...filters,from:dayInZone()}).rows.length,3);
    const secondBatch=createBatch(db,owner,"Nhóm cùng mã ở lô khác");
    db.run("UPDATE batch SET config=? WHERE id=?",JSON.stringify(cfg),secondBatch);
    addVideo(db,ownedBatch(db,owner,secondBatch),{filename:"texture.mp4",mime:"video/mp4",sample:"texture"});
    while(await tick(db,0)) {}
    const secondPlan=preview(db,ownedBatch(db,owner,secondBatch));
    publish(db,owner,secondBatch,false,secondPlan.reviewKey);
    while(await tick(db,0)) {}
    const acrossBatches=report(db,owner,{...filters,batch:""});
    assert.equal(acrossBatches.rows.length,28);
    const originalAdSet=result.rows[0].adSetId;
    assert.equal(report(db,owner,{...filters,batch:"",adSet:originalAdSet}).rows.length,21);
    assert.ok(reportCsv(result.rows).includes('"DEMO"'));
  } finally {db.sql.close();rmSync(root,{force:true,recursive:true});}
});
