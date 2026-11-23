import assert from "node:assert/strict";
import { test } from "node:test";
import { defaultConfig, configSchema, validation, dayInZone } from "../lib/domain.ts";
import { totals } from "../lib/metrics.ts";
import { MetaReadonly,encryptToken,decryptToken,vaultKey } from "../lib/meta-readonly.ts";

test("campaign objective, schedule, grouping, tracking and budgets are validated",()=>{
  const cfg=defaultConfig();
  assert.deepEqual(validation(cfg,["shared"],2).issues,[]);
  assert.equal(validation(cfg,["shared"],1).budget,validation(cfg,["shared"],24).budget);
  assert.equal(configSchema.safeParse({...cfg,account:"act_123"}).success,false);
  assert.equal(configSchema.safeParse({...cfg,budget:2000.5}).success,false);
  assert.equal(configSchema.safeParse({...cfg,start:"2026-02-30T10:30"}).success,false);
  const invalid={...cfg,optimization:"LINK_CLICKS" as const,dataset:"",budgetType:"lifetime" as const,spendCap:1200,
    tracking:"utm_source",groups:[{...cfg.groups[0],agesMin:45,agesMax:25}]};
  assert.equal(validation(invalid,["unknown"],1).issues.length,6);
  const grouped={...cfg,structure:"grouped" as const,scope:"adset" as const,groups:[cfg.groups[0],{...cfg.groups[0],key:"us-followers",budget:210000}]};
  assert.equal(validation(grouped,["shared","us-followers"],2).budget,360000);
  assert.equal(validation(grouped,["shared","us-followers","shared"],20).budget,360000);
  assert.equal(validation(grouped,["shared"],5).budget,150000);
  assert.equal(dayInZone(new Date("2026-11-21T17:00:00.000Z")),"2026-11-22");
});
test("reporting uses underlying denominators and no reach/currency mixing",()=>{
  const a={spend:200,impressions:500,linkClicks:8,purchases:2,value:400,reach:400,currency:"VND"};
  const b={spend:800,impressions:1500,linkClicks:12,purchases:1,value:100,reach:1300,currency:"VND"};
  const result=totals([a,b]);
  assert.deepEqual(result,{spend:1000,impressions:2000,linkClicks:20,purchases:3,value:500,reach:null,
    ctr:1,cpc:50,cpm:500,cpa:1000/3,roas:.5});
  assert.equal(totals([a]).reach,400);
  assert.equal(totals([a,{...b,purchases:null}]).cpa,null);
  assert.equal(totals([{...a,spend:0,linkClicks:0}]).cpc,null);
  assert.equal(totals([]).spend,null);
  assert.throws(()=>totals([a,{...b,currency:"USD"}]),/Mixed currencies/);
});
test("read-only integration encrypts tokens and follows cursors, never arbitrary next URLs",async ()=>{
  const secret=vaultKey("7f".repeat(32));
  const encrypted=encryptToken("fixture-token",secret);
  assert.equal(encrypted.includes(Buffer.from("fixture-token")),false);
  assert.equal(decryptToken(encrypted,secret),"fixture-token");
  const corrupt=Buffer.from(encrypted);corrupt[0]^=1;
  assert.throws(()=>decryptToken(corrupt,secret));
  const calls:URL[]=[];
  const request:typeof fetch=async (input,init)=>{
    const url=new URL(String(input));calls.push(url);
    assert.equal(url.hostname,"graph.facebook.com");assert.equal(url.searchParams.has("access_token"),false);
    assert.equal(init?.method,"GET");assert.equal(init?.redirect,"error");
    assert.equal(new Headers(init?.headers).get("authorization"),"Bearer fixture-token");
    if(!url.pathname.endsWith("/campaigns")) return Response.json({id:"act_123",name:"Mock",currency:"USD",timezone_name:"America/New_York",account_status:1});
    const next=url.searchParams.get("after");
    const data=[{id:next?"2":"1",name:"Mock",objective:"OUTCOME_SALES",effective_status:"PAUSED"}];
    return Response.json(next?{data}:{data,paging:{next:"https://example.invalid/ignore",cursors:{after:"cursor"}}});
  };
  const meta=new MetaReadonly("123","fixture-token",undefined,request);
  assert.equal((await meta.account()).currency,"USD");
  assert.deepEqual((await meta.campaigns()).items.map(c=>c.id),["1","2"]);
  assert.equal(calls[2].searchParams.get("after"),"cursor");
  const expired=new MetaReadonly("123","expired",undefined,async()=>Response.json({error:{code:190,message:"echo-token"}},{status:400}));
  await assert.rejects(expired.account(),(err:Error)=>err.message.includes("thu hồi")&&!err.message.includes("echo-token"));
});
