/* Presentation behavior adapted from AvianVisitors; see ../PROVENANCE.md. */
(function(){
  "use strict";
  var FLY_PROB=.15, poseBySpecies=Object.create(null), assetUrls=new Map(), refreshTimer=0, requestGeneration=0;
  var state={hours:+read("avian:hours","24"),locale:read("avian:locale","nl"),view:+read("avian:view","0"),sort:read("avian:sort","life"),chart:"timeline",recent:null,stats:null,lifelist:null};
  if([1,12,24,168,1000000].indexOf(state.hours)<0)state.hours=24;
  if(["nl","en","de"].indexOf(state.locale)<0)state.locale="nl";
  if(state.view<0||state.view>2)state.view=0;
  var collage=document.getElementById("collage"),message=document.getElementById("message"),access=document.getElementById("access"),tokenInput=document.getElementById("token"),stage=document.querySelector(".stage");
  var views=document.getElementById("views"),viewTitle=document.getElementById("viewTitle"),viewSubtitle=document.getElementById("viewSubtitle");
  var detail=window.AvianDetail.create({fetchJson:fetchJson,loadAsset:loadAsset,fetchBlob:function(url){return fetch(url,{headers:headers()}).then(function(response){if(!response.ok)throw new Error("Opname niet beschikbaar");return response.blob();});},locale:function(){return state.locale;}});

  function read(key,fallback){try{return localStorage.getItem(key)||fallback;}catch(_){return fallback;}}
  function write(key,value){try{localStorage.setItem(key,String(value));}catch(_){}}
  function sessionRead(key){try{return sessionStorage.getItem(key)||"";}catch(_){return"";}}
  function sessionWrite(key,value){try{if(value)sessionStorage.setItem(key,value);else sessionStorage.removeItem(key);}catch(_){}}
  function hash(text){var value=2166136261;for(var i=0;i<text.length;i++)value=Math.imul(value^text.charCodeAt(i),16777619);return value>>>0;}
  function esc(value){var div=document.createElement("div");div.textContent=value==null?"":String(value);return div.innerHTML;}
  function number(value){return new Intl.NumberFormat(state.locale).format(value||0);}
  function date(value){if(!value)return"—";return new Intl.DateTimeFormat(state.locale,{day:"numeric",month:"short",year:"numeric"}).format(new Date(value));}
  function windowLabel(){return state.hours===1?"het afgelopen uur":state.hours===12?"de afgelopen 12 uur":state.hours===24?"de afgelopen 24 uur":state.hours===168?"de afgelopen 7 dagen":"alle waarnemingen";}
  function syncPill(root){if(!root)return;var pill=root.querySelector("i"),active=root.querySelector("button[aria-current=true]");if(!pill||!active)return;pill.style.width=active.offsetWidth+"px";pill.style.transform="translateX("+active.offsetLeft+"px)";}
  function select(root,attribute,value){root.querySelectorAll("button").forEach(function(button){button.setAttribute("aria-current",button.dataset[attribute]===String(value)?"true":"false");});syncPill(root);}
  function token(){return tokenInput.value.trim()||sessionRead("backyardApiToken");}
  function headers(){return{Authorization:"Bearer "+token()};}
  function clearAssets(){assetUrls.forEach(URL.revokeObjectURL);assetUrls.clear();}
  function fetchJson(url,options){var requestOptions=Object.assign({},options||{},{headers:headers()});return fetch(url,requestOptions).then(function(response){if(response.status===401)throw new Error("Token niet geaccepteerd.");if(!response.ok)throw new Error("Backyard kon de gegevens niet laden.");return response.json();});}
  function loadAsset(url,image){
    if(!url)return Promise.reject(new Error("Geen asset"));
    if(assetUrls.has(url)){image.src=assetUrls.get(url);image.classList.add("ready");return Promise.resolve();}
    return fetch(url,{headers:headers()}).then(function(response){if(!response.ok)throw new Error("Asset niet beschikbaar");return response.blob();}).then(function(blob){var objectUrl=URL.createObjectURL(blob);assetUrls.set(url,objectUrl);image.src=objectUrl;image.classList.add("ready");});
  }

  function setView(index,persist){
    state.view=Math.max(0,Math.min(2,index));if(persist!==false)write("avian:view",state.view);
    stage.classList.toggle("is-collage",state.view===0);
    views.style.transform="translateX(-"+(state.view*100)+"%)";
    select(document.getElementById("viewNav"),"view",state.view);
    viewTitle.textContent=state.view===2?"Vogelatlas":"Recent gehoord";
    viewSubtitle.textContent=state.view===2?(state.lifelist?number(state.lifelist.species_count)+" soorten in de lifelist":"alle geaccepteerde soorten"):windowLabel();
    requestAnimationFrame(syncCompactHeader);
    if(state.view===0)animateCollage();else if(state.view===1)animateStats();else animateAtlas();
  }
  function syncCompactHeader(){var view=document.getElementById("v"+state.view);if(!view)return;var canScroll=view.scrollHeight>view.clientHeight+3,threshold=stage.classList.contains("is-compact")?8:26;stage.classList.toggle("is-compact",canScroll&&view.scrollTop>threshold);}
  ["v0","v1","v2"].forEach(function(id){document.getElementById(id).addEventListener("scroll",syncCompactHeader,{passive:true});});
  function animateCollage(){var els=[].slice.call(document.querySelectorAll(".gtile")),cx=collage.clientWidth/2,cy=collage.clientHeight/2,max=1,info=els.map(function(el){var d=Math.hypot(el.offsetLeft+el.offsetWidth/2-cx,el.offsetTop+el.offsetHeight/2-cy);max=Math.max(max,d);return{el:el,d:d};});info.forEach(function(o){o.el.classList.remove("entering");o.el.style.animationDelay=Math.round(o.d/max*520)+"ms";});void collage.offsetWidth;info.forEach(function(o){o.el.classList.add("entering");});}
  function animateStats(){document.querySelectorAll(".timeline-column b,.rhythm i").forEach(function(el,i){el.style.animationDelay=(i*18)+"ms";el.style.animationName="none";void el.offsetWidth;el.style.animationName="bar-in";});}
  function animateAtlas(){var cards=[].slice.call(document.querySelectorAll(".stamp-card"));var rows={};cards.map(function(card){return card.offsetTop;}).sort(function(a,b){return a-b;}).forEach(function(top){if(rows[top]===undefined)rows[top]=Object.keys(rows).length;});cards.forEach(function(el){el.classList.remove("entering");el.style.animationDelay=(Math.min(rows[el.offsetTop]||0,10)*90)+"ms";});void document.getElementById("atlasGrid").offsetWidth;cards.forEach(function(el){el.classList.add("entering");});}

  /* Original AvianVisitors raster-mask nester, adapted only at the data edge. */
  function maskCells(mask){
    if(!mask||!mask.bits||!mask.w||!mask.h)return null;
    try{var raw=atob(mask.bits),cells=[];for(var i=0;i<mask.w*mask.h;i++)if(raw.charCodeAt(i>>3)&(128>>(i&7)))cells.push([i%mask.w,Math.floor(i/mask.w)]);return cells.length?{w:mask.w,h:mask.h,cells:cells}:null;}catch(_){return null;}
  }
  function chooseAsset(item){
    var perched=item.assets&&item.assets.perched,flight=item.assets&&item.assets.flight,pose=poseBySpecies[item.scientific_name];
    if(!pose){pose=perched&&flight?(hash(item.scientific_name)%1000<FLY_PROB*1000?"flight":"perched"):perched?"perched":flight?"flight":null;poseBySpecies[item.scientific_name]=pose;}
    if(pose==="perched"&&!perched)pose=flight?"flight":null;if(pose==="flight"&&!flight)pose=perched?"perched":null;
    return pose?{pose:pose,data:item.assets[pose]}:null;
  }
  function tuning(n){return{packingBudgetFrac:n<=4?.46:n<=12?.40:n<=24?.34:.28,countExp:.65,minTileAreaFrac:n<=8?.0100:n<=20?.0075:.0055,ellipseAspectBias:2.1};}
  var GRID_STRIDE=4,COLLAGE_PAD=3;
  function maskPack(tiles,W,H,xBias,yBias,pad){
    var GW=Math.ceil(W/GRID_STRIDE)+2,GH=Math.ceil(H/GRID_STRIDE)+2,grid=new Uint8Array(GW*GH);
    function range(tile,tx,ty,c){var sx=tile.fullW/tile.mask.w,sy=tile.fullH/tile.mask.h,x0=(tx+c[0]*sx)/GRID_STRIDE|0,y0=(ty+c[1]*sy)/GRID_STRIDE|0,x1=(tx+(c[0]+1)*sx)/GRID_STRIDE|0,y1=(ty+(c[1]+1)*sy)/GRID_STRIDE|0;return[Math.max(0,x0),Math.max(0,y0),Math.min(GW-1,x1),Math.min(GH-1,y1)];}
    function collides(tile,tx,ty){for(var i=0;i<tile.mask.cells.length;i++){var r=range(tile,tx,ty,tile.mask.cells[i]);for(var y=r[1];y<=r[3];y++)for(var x=r[0];x<=r[2];x++)if(grid[y*GW+x])return true;}return false;}
    function stamp(tile,tx,ty){for(var i=0;i<tile.mask.cells.length;i++){var r=range(tile,tx,ty,tile.mask.cells[i]),y0=Math.max(0,r[1]-pad),y1=Math.min(GH-1,r[3]+pad),x0=Math.max(0,r[0]-pad),x1=Math.min(GW-1,r[2]+pad);for(var y=y0;y<=y1;y++)for(var x=x0;x<=x1;x++)grid[y*GW+x]=1;}}
    function off(tile,x,y){return x<0||y<0||x+tile.fullW>W||y+tile.fullH>H;}
    var cx=W/2,cy=H/2,placed=[],seed=0x9E3779B9;tiles.sort(function(a,b){return b.fullW*b.fullH-a.fullW*a.fullH;});function rand(){seed=seed*16807%2147483647;return seed/2147483647;}
    tiles.forEach(function(tile,index){if(!index){tile.x=cx-tile.fullW/2;tile.y=cy-tile.fullH/2;stamp(tile,tile.x,tile.y);placed.push(tile);return;}var comX=0,comY=0,comW=0;placed.forEach(function(p){var a=p.fullW*p.fullH;comX+=(p.x+p.fullW/2)*a;comY+=(p.y+p.fullH/2)*a;comW+=a;});comX/=comW;comY/=comW;var best=null,bestCost=Infinity,step=Math.max(GRID_STRIDE,Math.min(tile.fullW,tile.fullH)*.05),maxR=Math.max(W,H),foundRing=-1,phase=rand()*Math.PI*2;for(var radius=0;radius<=maxR;radius+=step){if(foundRing>=0&&radius>foundRing+step*2)break;var samples=Math.max(36,Math.floor(radius/1.6));for(var k=0;k<samples;k++){var theta=phase+k/samples*Math.PI*2,x=cx+radius*xBias*Math.cos(theta)-tile.fullW/2,y=cy+radius*yBias*Math.sin(theta)-tile.fullH/2;if(off(tile,x,y)||collides(tile,x,y))continue;var dx=x+tile.fullW/2-comX,dy=y+tile.fullH/2-comY,cost=Math.hypot(dx/xBias,dy/yBias)+rand()*step*.5;if(cost<bestCost){bestCost=cost;best={x:x,y:y};}}if(best&&foundRing<0)foundRing=radius;}if(best){tile.x=best.x;tile.y=best.y;stamp(tile,best.x,best.y);}else{tile.x=-99999;tile.y=-99999;}placed.push(tile);});return placed;
  }
  function edgePath(mask){
    if(!mask)return"M 8 18 Q 50 2 92 18";var tops=new Array(mask.w),i,x;
    for(i=0;i<mask.cells.length;i++){x=mask.cells[i][0];var y=mask.cells[i][1];if(tops[x]===undefined||y<tops[x])tops[x]=y;}
    var points=[];for(x=0;x<mask.w;x+=Math.max(1,Math.floor(mask.w/14)))if(tops[x]!==undefined)points.push([(x/mask.w)*100,Math.max(4,(tops[x]/mask.h)*100-3)]);
    return points.length<2?"M 8 18 Q 50 2 92 18":"M "+points.map(function(p){return p[0].toFixed(1)+" "+p[1].toFixed(1);}).join(" L ");
  }
  function renderCollage(){
    collage.replaceChildren();var items=state.recent&&state.recent.species||[];
    if(!items.length){collage.innerHTML='<div class="empty-state">Geen geaccepteerde vogelwaarnemingen in deze periode.</div>';return;}
    var rect=collage.getBoundingClientRect(),W=rect.width,H=rect.height;if(!W||!H)return;
    var T=tuning(items.length),budget=W*H*T.packingBudgetFrac,minArea=W*H*T.minTileAreaFrac;
    var arranged=items.map(function(item){var selected=chooseAsset(item),data=selected&&selected.data,dims=data&&data.dimensions,mask=maskCells(data&&data.mask);if(!selected||!mask)return null;return{item:item,selected:selected,mask:mask,ar:dims&&dims[0]>0&&dims[1]>0?dims[0]/dims[1]:1.4,score:Math.pow(Math.max(1,+item.count||1),T.countExp)};}).filter(Boolean);
    var sumScore=arranged.reduce(function(a,t){return a+t.score;},0)||1;arranged.forEach(function(t){t.area=Math.max(minArea,budget*t.score/sumScore);});var sumArea=arranged.reduce(function(a,t){return a+t.area;},0);if(sumArea>budget){var fixed=arranged.filter(function(t){return t.area<=minArea+1e-9;}).reduce(function(a,t){return a+t.area;},0),flex=sumArea-fixed,scaleFlex=flex>0?Math.min(1,Math.max(0,budget-fixed)/flex):1;arranged.forEach(function(t){if(t.area>minArea+1e-9)t.area*=scaleFlex;});}arranged.forEach(function(t){t.fullW=Math.sqrt(t.area*t.ar);t.fullH=t.fullW/t.ar;});
    var narrow=W<=700,xBias=narrow?1:T.ellipseAspectBias,yBias=narrow?1.7:1,pad=narrow?Math.max(1,COLLAGE_PAD-1):COLLAGE_PAD,placed=maskPack(arranged,W,H,xBias,yBias,pad);
    function bounds(all){var L=Infinity,R=-Infinity,Top=Infinity,B=-Infinity;all.forEach(function(t){if(t.x<-1000)return;L=Math.min(L,t.x);R=Math.max(R,t.x+t.fullW);Top=Math.min(Top,t.y);B=Math.max(B,t.y+t.fullH);});return{L:L,R:R,T:Top,B:B};}
    var b=bounds(placed);for(var iter=0;iter<10;iter++){var missing=placed.some(function(t){return t.x<-1000;}),overflow=b.L<0||b.T<0||b.R>W||b.B>H;if(!missing&&!overflow)break;var scale=.93;if(overflow){var sx=W*.96/Math.max(b.R-b.L,W*.96),sy=H*.94/Math.max(b.B-b.T,H*.94);scale=Math.min(scale,sx,sy);}arranged.forEach(function(t){t.fullW*=scale;t.fullH*=scale;});placed=maskPack(arranged,W,H,xBias,yBias,pad);b=bounds(placed);}
    if(isFinite(b.L)){var shiftX=W/2-(b.L+b.R)/2,shiftY=H/2-(b.T+b.B)/2;placed.forEach(function(t){if(t.x>-1000){t.x+=shiftX;t.y+=shiftY;}});}
    var missingItems=items.filter(function(item){return !arranged.some(function(t){return t.item===item&&t.x>-1000;});});
    placed.filter(function(t){return t.x>-1000;}).forEach(function(tile,index){
      var el=document.createElement("button");el.type="button";el.className="gtile";el.dataset.scientificName=tile.item.scientific_name;el.style.cssText="left:"+tile.x+"px;top:"+tile.y+"px;width:"+tile.fullW+"px;height:"+tile.fullH+"px";el.setAttribute("aria-label",tile.item.common_name+", "+tile.item.count+" waarnemingen");el.addEventListener("click",function(){detail.open(tile.item.scientific_name,el);});
      if(tile.selected){
        var image=document.createElement("img");image.alt="";el.appendChild(image);loadAsset(tile.selected.data.url,image).catch(function(){el.classList.add("missing");image.remove();var fallback=document.createElement("span");fallback.textContent=tile.item.common_name;el.appendChild(fallback);});
        var id="edge-"+index,svg=document.createElementNS("http://www.w3.org/2000/svg","svg"),targetPx=Math.max(11,Math.min(18,11+Math.log2(Math.max(1,+tile.item.count||1))*1.35)),sizeByTile=targetPx*100/(Math.max(70,tile.fullW)*1.24),fontSize=Math.max(3.8,Math.min(8.2,sizeByTile,104/tile.item.common_name.length));svg.setAttribute("class","gtile-label");svg.setAttribute("viewBox","0 0 100 100");svg.innerHTML='<defs><path id="'+id+'" d="'+edgePath(tile.mask)+'"/></defs><text style="font-size:'+fontSize.toFixed(2)+'px"><textPath href="#'+id+'" startOffset="50%" text-anchor="middle">'+esc(tile.item.common_name)+'</textPath></text>';el.appendChild(svg);
      }
      collage.appendChild(el);
    });missingItems.forEach(function(item,index){var el=document.createElement("button");el.type="button";el.className="missing-bird";el.dataset.scientificName=item.scientific_name;el.textContent=item.common_name;el.style.cssText="width:92px;height:92px;left:"+(12+(index%3)*102)+"px;bottom:"+(12+Math.floor(index/3)*102)+"px";el.addEventListener("click",function(){detail.open(item.scientific_name,el);});collage.appendChild(el);});
    var tip=document.createElement("div");tip.className="collage-tip";tip.setAttribute("aria-hidden","true");collage.appendChild(tip);animateCollage();
  }

  function renderTimeline(){
    var root=document.getElementById("statsTimeline"),items=state.stats&&state.stats.timeline||[];root.replaceChildren();
    if(!items.length){root.innerHTML='<div class="empty-state">Geen statistieken voor deze periode.</div>';return;}
    var max=Math.max.apply(null,items.map(function(item){return item.count;}))||1;
    var labelStep=Math.max(1,Math.ceil(items.length/7));
    items.forEach(function(item,index){var column=document.createElement("div");column.className="timeline-column";var pct=Math.max(1,item.count/max*100),when=new Date(item.bucket_start),show=index%labelStep===0||index===items.length-1;column.style.left=((index+.5)/items.length*100)+"%";column.style.width=(100/items.length)+"%";column.innerHTML='<b style="height:'+pct+'%;animation-delay:'+(index*18)+'ms"></b><em style="bottom:calc('+pct+'% + 5px)">'+(item.count?number(item.count):"")+'</em><span>'+(show?esc(state.stats.timeline_granularity==="hour"?String(when.getHours()).padStart(2,"0")+":00":new Intl.DateTimeFormat(state.locale,{day:"numeric",month:"short"}).format(when)):"")+'</span>';root.appendChild(column);});
  }
  function renderHeatmap(){
    var root=document.getElementById("statsHeatmap"),rows=state.stats&&state.stats.hourly_species||[];root.replaceChildren();
    if(!rows.length){root.innerHTML='<div class="empty-state">Geen uurgegevens voor deze periode.</div>';return;}
    var max=1;rows.forEach(function(row){max=Math.max(max,Math.max.apply(null,row.counts));});
    var head=document.createElement("div");head.className="heat-row heat-hours";head.innerHTML='<span>soort</span>'+Array.from({length:24},function(_,hour){return'<i class="heat-cell">'+(hour%3===0?String(hour).padStart(2,"0"):"")+'</i>';}).join("");root.appendChild(head);
    rows.sort(function(a,b){return b.counts.reduce(sum,0)-a.counts.reduce(sum,0);}).forEach(function(row){var el=document.createElement("div");el.className="heat-row";el.innerHTML='<span title="'+esc(row.common_name)+'">'+esc(row.common_name)+'</span>'+row.counts.map(function(count){return'<i class="heat-cell" style="--heat:'+(count/max)+'" title="'+count+'">'+count+'</i>';}).join("");root.appendChild(el);});
  }
  function sum(a,b){return a+b;}
  function list(root,items,value){root.innerHTML=items.length?items.map(function(item){return'<li><span>'+esc(item.common_name)+'</span><em>'+esc(item.scientific_name)+'</em><b>'+esc(value(item))+'</b></li>';}).join(""):'<li><span>Geen waarnemingen</span></li>';}
  function renderStats(){
    var stats=state.stats;if(!stats)return;renderTimeline();renderHeatmap();
    document.getElementById("periodStats").innerHTML='<div><dt>'+esc(windowLabel())+'</dt><dd>'+number(stats.observation_count)+'</dd></div><div><dt>soorten in periode</dt><dd>'+number(stats.species_count)+'</dd></div><div><dt>alle waarnemingen</dt><dd>'+number(stats.all_time_observation_count)+'</dd></div><div><dt>lifelist</dt><dd>'+number(stats.all_time_species_count)+'</dd></div>';
    list(document.getElementById("topSpecies"),stats.species.slice(0,8),function(item){return number(item.count);});
    list(document.getElementById("newestSpecies"),stats.newest_species.slice(0,6),function(item){return date(item.first_observed_at);});
    var rhythm=document.getElementById("rhythm"),max=Math.max.apply(null,stats.rhythm)||1;rhythm.innerHTML=stats.rhythm.map(function(count,hour){return'<i data-hour="'+String(hour).padStart(2,"0")+'" title="'+hour+':00 · '+count+'" style="height:'+Math.max(1,count/max*100)+'%;animation-delay:'+(hour*15)+'ms"></i>';}).join("");
    applyChart();
  }
  function applyChart(){var timeline=state.chart==="timeline";document.getElementById("statsTimeline").hidden=!timeline;document.getElementById("statsHeatmap").hidden=timeline;select(document.getElementById("chartPick"),"chart",state.chart);}

  function atlasAsset(item,preferred){if(!item.assets)return null;return item.assets[preferred]||item.assets[preferred==="perched"?"flight":"perched"]||null;}
  function renderAtlas(){
    var grid=document.getElementById("atlasGrid"),items=(state.lifelist&&state.lifelist.species||[]).slice();grid.replaceChildren();
    document.getElementById("atlasCount").textContent=items.length?number(items.length)+(items.length===1?" soort":" soorten"):"";
    if(!items.length){grid.innerHTML='<div class="empty-state">De atlas vult zich zodra Backyard een vogel accepteert.</div>';return;}
    var accession={};items.forEach(function(item,index){accession[item.scientific_name]=index+1;});
    if(state.sort==="alpha")items.sort(function(a,b){return a.common_name.localeCompare(b.common_name,state.locale);});
    else if(state.sort==="count")items.sort(function(a,b){return b.count-a.count||a.common_name.localeCompare(b.common_name,state.locale);});
    else items.sort(function(a,b){return new Date(b.first_observed_at)-new Date(a.first_observed_at)||b.scientific_name.localeCompare(a.scientific_name);});
    items.forEach(function(item,index){
      var issue=hash(item.scientific_name)%5+1,card=document.createElement("article");card.className="stamp-card issue-"+issue;card.dataset.scientificName=item.scientific_name;card.tabIndex=0;card.setAttribute("role","button");card.setAttribute("aria-label",item.common_name+", "+item.count+" waarnemingen");
      card.innerHTML='<div class="stamp"><div class="stamp-inner"><p class="stamp-issue">VOGELBEZOEKEN</p><b class="stamp-number">'+String(accession[item.scientific_name]).padStart(2,"0")+'</b><div class="stamp-art"></div><footer class="stamp-caption"><h2>'+esc(item.common_name)+'</h2><em>'+esc(item.scientific_name)+'</em><span>'+number(item.count)+'×</span></footer></div></div>';
      var art=card.querySelector(".stamp-art"),preferred="perched",asset=atlasAsset(item,preferred);
      function paint(next){art.replaceChildren();if(!next){art.innerHTML='<span class="no-art">illustratie onderweg</span>';return;}var image=document.createElement("img");image.alt="";art.appendChild(image);loadAsset(next.url,image).catch(function(){art.innerHTML='<span class="no-art">illustratie niet beschikbaar</span>';});}
      paint(asset);
      card.title="Open soortdetail";card.addEventListener("click",function(){detail.open(item.scientific_name,card);});card.addEventListener("keydown",function(event){if(event.key==="Enter"||event.key===" "){event.preventDefault();detail.open(item.scientific_name,card);}});
      grid.appendChild(card);
    });animateAtlas();
  }

  function renderAll(){renderCollage();renderStats();renderAtlas();setView(state.view,false);}
  function refresh(){
    var generation=++requestGeneration,currentToken=token();if(!currentToken)return;
    message.hidden=false;message.textContent="Vogels laden…";
    var suffix="?hours="+state.hours+"&locale="+state.locale;
    Promise.all([fetchJson("/api/avian-visitors/recent"+suffix),fetchJson("/api/avian-visitors/stats"+suffix),fetchJson("/api/avian-visitors/lifelist?locale="+state.locale)]).then(function(parts){
      if(generation!==requestGeneration)return;state.recent=parts[0];state.stats=parts[1];state.lifelist=parts[2];message.hidden=true;access.classList.add("connected");access.querySelector("button").textContent="verversen";renderAll();clearTimeout(refreshTimer);refreshTimer=setTimeout(refresh,60000);
    }).catch(function(error){if(generation!==requestGeneration)return;message.textContent=error.message;message.hidden=false;if(/Token/.test(error.message)){access.classList.remove("connected");sessionWrite("backyardApiToken","");}});
  }

  document.getElementById("viewNav").addEventListener("click",function(event){var button=event.target.closest("button[data-view]");if(button)setView(+button.dataset.view,true);});
  document.getElementById("windowPick").addEventListener("click",function(event){var button=event.target.closest("button[data-hours]");if(!button)return;state.hours=+button.dataset.hours;write("avian:hours",state.hours);select(this,"hours",state.hours);viewSubtitle.textContent=windowLabel();refresh();});
  document.getElementById("localePick").addEventListener("click",function(event){var button=event.target.closest("button[data-locale]");if(!button)return;detail.close();state.locale=button.dataset.locale;write("avian:locale",state.locale);select(this,"locale",state.locale);refresh();});
  document.getElementById("atlasSort").addEventListener("click",function(event){var button=event.target.closest("button[data-sort]");if(!button)return;state.sort=button.dataset.sort;write("avian:sort",state.sort);select(this,"sort",state.sort);renderAtlas();});
  document.getElementById("chartPick").addEventListener("click",function(event){var button=event.target.closest("button[data-chart]");if(!button)return;state.chart=button.dataset.chart;applyChart();});
  access.addEventListener("submit",function(event){event.preventDefault();var value=token();if(value){if(value!==sessionRead("backyardApiToken"))clearAssets();sessionWrite("backyardApiToken",value);refresh();}});
  var resizeTimer;window.addEventListener("resize",function(){clearTimeout(resizeTimer);resizeTimer=setTimeout(function(){if(state.recent)renderCollage();["windowPick","localePick","viewNav","chartPick","atlasSort"].forEach(function(id){syncPill(document.getElementById(id));});syncCompactHeader();},180);});
  window.addEventListener("beforeunload",clearAssets);
  select(document.getElementById("windowPick"),"hours",state.hours);select(document.getElementById("localePick"),"locale",state.locale);select(document.getElementById("atlasSort"),"sort",state.sort);setView(state.view,false);
  var saved=sessionRead("backyardApiToken");if(saved){tokenInput.value=saved;refresh();}
}());
