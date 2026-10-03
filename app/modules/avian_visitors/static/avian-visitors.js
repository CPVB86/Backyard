/* Presentation behavior adapted from AvianVisitors; see ../PROVENANCE.md. */
(function(){
  "use strict";
  var FLY_PROB=.15, poseBySpecies=Object.create(null), assetUrls=new Map(), refreshTimer=0, requestGeneration=0;
  var state={hours:+read("avian:hours","24"),locale:read("avian:locale","nl"),view:+read("avian:view","0"),sort:read("avian:sort","life"),chart:"timeline",recent:null,stats:null,lifelist:null};
  if([1,12,24,168,1000000].indexOf(state.hours)<0)state.hours=24;
  if(["nl","en","de"].indexOf(state.locale)<0)state.locale="nl";
  if(state.view<0||state.view>2)state.view=0;
  var collage=document.getElementById("collage"),message=document.getElementById("message"),access=document.getElementById("access"),tokenInput=document.getElementById("token");
  var views=document.getElementById("views"),viewTitle=document.getElementById("viewTitle"),viewSubtitle=document.getElementById("viewSubtitle");

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
  function fetchJson(url){return fetch(url,{headers:headers()}).then(function(response){if(response.status===401)throw new Error("Token niet geaccepteerd.");if(!response.ok)throw new Error("Backyard kon de gegevens niet laden.");return response.json();});}
  function loadAsset(url,image){
    if(!url)return Promise.reject(new Error("Geen asset"));
    if(assetUrls.has(url)){image.src=assetUrls.get(url);image.classList.add("ready");return Promise.resolve();}
    return fetch(url,{headers:headers()}).then(function(response){if(!response.ok)throw new Error("Asset niet beschikbaar");return response.blob();}).then(function(blob){var objectUrl=URL.createObjectURL(blob);assetUrls.set(url,objectUrl);image.src=objectUrl;image.classList.add("ready");});
  }

  function setView(index,persist){
    state.view=Math.max(0,Math.min(2,index));if(persist!==false)write("avian:view",state.view);
    views.style.transform="translateX(-"+(state.view*33.333333)+"%)";
    select(document.getElementById("viewNav"),"view",state.view);
    viewTitle.textContent=state.view===2?"Vogelatlas":"Recent gehoord";
    viewSubtitle.textContent=state.view===2?(state.lifelist?number(state.lifelist.species_count)+" soorten in de lifelist":"alle geaccepteerde soorten"):windowLabel();
    if(state.view===0)animateCollage();else if(state.view===1)animateStats();else animateAtlas();
  }
  function animateCollage(){document.querySelectorAll(".bird").forEach(function(el,i){el.classList.remove("entered");setTimeout(function(){el.classList.add("entered");},35+i*48);});}
  function animateStats(){document.querySelectorAll(".timeline-column b,.rhythm i").forEach(function(el,i){el.style.animationDelay=(i*18)+"ms";el.style.animationName="none";void el.offsetWidth;el.style.animationName="bar-in";});}
  function animateAtlas(){document.querySelectorAll(".stamp-card").forEach(function(el,i){el.classList.remove("entered");setTimeout(function(){el.classList.add("entered");},80+i*55);});}

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
  function collageTiles(items,width,height){
    var n=items.length,coverage=n<=3?.43:n<=7?.55:n<=14?.64:.69,available=width*height*coverage;
    var weights=items.map(function(item){return Math.pow(item.count,.65);}),total=weights.reduce(function(a,b){return a+b;},0)||1;
    return items.map(function(item,index){
      var selected=chooseAsset(item),data=selected&&selected.data,dims=data&&data.dimensions,ratio=dims&&dims[0]>0&&dims[1]>0?dims[0]/dims[1]:1;
      var area=available*weights[index]/total,w=Math.sqrt(area*ratio),h=w/ratio,cap=Math.min(width*(n<4?.48:.38),height*(n<4?.7:.56)),scale=Math.min(1,cap/Math.max(w,h));
      return{item:item,selected:selected,mask:maskCells(data&&data.mask),w:Math.max(68,w*scale),h:Math.max(68,h*scale)};
    }).sort(function(a,b){return b.item.count-a.item.count||a.item.scientific_name.localeCompare(b.item.scientific_name);});
  }
  function pack(all,width,height){
    var unit=5,gw=Math.ceil(width/unit),gh=Math.ceil(height/unit),occupied=new Uint8Array(gw*gh);
    function samples(tile,x,y){var out=[],mask=tile.mask;if(mask){var step=Math.max(1,Math.floor(mask.cells.length/1900));for(var i=0;i<mask.cells.length;i+=step)out.push([Math.floor((x+mask.cells[i][0]/mask.w*tile.w)/unit),Math.floor((y+mask.cells[i][1]/mask.h*tile.h)/unit)]);}else for(var yy=y;yy<y+tile.h;yy+=unit*2)for(var xx=x;xx<x+tile.w;xx+=unit*2)out.push([Math.floor(xx/unit),Math.floor(yy/unit)]);return out;}
    all.forEach(function(tile,order){
      var angle=(hash(tile.item.scientific_name)%6283)/1000,found=null;
      for(var attempt=0;attempt<1100;attempt++){
        var radius=3*Math.sqrt(attempt),a=angle+attempt*2.399963,x=width/2-tile.w/2+Math.cos(a)*radius,y=height/2-tile.h/2+Math.sin(a)*radius*.7;
        if(x<0||y<0||x+tile.w>width||y+tile.h>height)continue;var pts=samples(tile,x,y),collision=false;
        for(var i=0;i<pts.length;i++){var px=pts[i][0],py=pts[i][1];if(px<0||py<0||px>=gw||py>=gh||occupied[py*gw+px]){collision=true;break;}}
        if(!collision){found={x:x,y:y,pts:pts};break;}
      }
      if(!found){var columns=Math.max(1,Math.floor(width/Math.max(tile.w,120))),fx=(order%columns)*width/columns,fy=Math.floor(order/columns)*Math.min(tile.h,height/3);found={x:Math.min(width-tile.w,Math.max(0,fx)),y:Math.min(height-tile.h,Math.max(0,fy)),pts:[]};}
      tile.x=found.x;tile.y=found.y;found.pts.forEach(function(p){for(var dy=-3;dy<=3;dy++)for(var dx=-3;dx<=3;dx++)if(p[0]+dx>=0&&p[1]+dy>=0&&p[0]+dx<gw&&p[1]+dy<gh)occupied[(p[1]+dy)*gw+p[0]+dx]=1;});
    });return all;
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
    var rect=collage.getBoundingClientRect(),arranged=pack(collageTiles(items,rect.width,rect.height),rect.width,rect.height);
    arranged.forEach(function(tile,index){
      var el=document.createElement("article");el.className="bird"+(tile.selected?"":" missing");el.style.cssText="left:"+tile.x+"px;top:"+tile.y+"px;width:"+tile.w+"px;height:"+tile.h+"px";el.setAttribute("aria-label",tile.item.common_name+", "+tile.item.count+" waarnemingen");
      if(tile.selected){
        var image=document.createElement("img");image.alt="";el.appendChild(image);loadAsset(tile.selected.data.url,image).catch(function(){el.classList.add("missing");image.remove();var fallback=document.createElement("span");fallback.textContent=tile.item.common_name;el.appendChild(fallback);});
        var id="edge-"+index,svg=document.createElementNS("http://www.w3.org/2000/svg","svg");svg.setAttribute("viewBox","0 0 100 100");svg.innerHTML='<defs><path id="'+id+'" d="'+edgePath(tile.mask)+'"/></defs><text style="font-size:'+Math.max(4.8,Math.min(8,120/tile.item.common_name.length))+'px"><textPath href="#'+id+'" startOffset="50%" text-anchor="middle">'+esc(tile.item.common_name)+'</textPath></text>';el.appendChild(svg);
      }else{var span=document.createElement("span");span.textContent=tile.item.common_name;el.appendChild(span);}
      collage.appendChild(el);
    });animateCollage();
  }

  function renderTimeline(){
    var root=document.getElementById("statsTimeline"),items=state.stats&&state.stats.timeline||[];root.replaceChildren();
    if(!items.length){root.innerHTML='<div class="empty-state">Geen statistieken voor deze periode.</div>';return;}
    var max=Math.max.apply(null,items.map(function(item){return item.count;}))||1;
    var labelStep=Math.max(1,Math.ceil(items.length/7));
    items.forEach(function(item,index){var column=document.createElement("div");column.className="timeline-column";var pct=Math.max(1,item.count/max*100),when=new Date(item.bucket_start),show=index%labelStep===0||index===items.length-1;column.style.setProperty("--bar-height",pct+"%");column.innerHTML='<b style="height:'+pct+'%;animation-delay:'+(index*18)+'ms"></b><em>'+(item.count?number(item.count):"")+'</em><span>'+(show?esc(state.stats.timeline_granularity==="hour"?String(when.getHours()).padStart(2,"0")+":00":new Intl.DateTimeFormat(state.locale,{day:"numeric",month:"short"}).format(when)):"")+'</span>';root.appendChild(column);});
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
      var issue=hash(item.scientific_name)%6,card=document.createElement("article");card.className="stamp-card issue-"+issue;card.setAttribute("aria-label",item.common_name+", "+item.count+" waarnemingen");
      card.innerHTML='<div class="stamp"><div class="stamp-inner"><p class="stamp-issue">VOGELBEZOEKEN</p><b class="stamp-number">'+String(accession[item.scientific_name]).padStart(2,"0")+'</b><div class="stamp-art"></div><footer class="stamp-caption"><h2>'+esc(item.common_name)+'</h2><em>'+esc(item.scientific_name)+'</em><span>'+number(item.count)+'×</span></footer></div></div>';
      var art=card.querySelector(".stamp-art"),preferred="perched",asset=atlasAsset(item,preferred);
      function paint(next){art.replaceChildren();if(!next){art.innerHTML='<span class="no-art">illustratie<br>onderweg</span>';return;}var image=document.createElement("img");image.alt="";art.appendChild(image);loadAsset(next.url,image).catch(function(){art.innerHTML='<span class="no-art">illustratie<br>niet beschikbaar</span>';});}
      paint(asset);
      if(item.assets&&item.assets.perched&&item.assets.flight){card.title="Klik om zittend/vliegend te wisselen";card.addEventListener("click",function(){preferred=preferred==="perched"?"flight":"perched";paint(atlasAsset(item,preferred));});}
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
  document.getElementById("localePick").addEventListener("click",function(event){var button=event.target.closest("button[data-locale]");if(!button)return;state.locale=button.dataset.locale;write("avian:locale",state.locale);select(this,"locale",state.locale);refresh();});
  document.getElementById("atlasSort").addEventListener("click",function(event){var button=event.target.closest("button[data-sort]");if(!button)return;state.sort=button.dataset.sort;write("avian:sort",state.sort);select(this,"sort",state.sort);renderAtlas();});
  document.getElementById("chartPick").addEventListener("click",function(event){var button=event.target.closest("button[data-chart]");if(!button)return;state.chart=button.dataset.chart;applyChart();});
  access.addEventListener("submit",function(event){event.preventDefault();var value=token();if(value){if(value!==sessionRead("backyardApiToken"))clearAssets();sessionWrite("backyardApiToken",value);refresh();}});
  var resizeTimer;window.addEventListener("resize",function(){clearTimeout(resizeTimer);resizeTimer=setTimeout(function(){if(state.recent)renderCollage();["windowPick","localePick","viewNav","chartPick","atlasSort"].forEach(function(id){syncPill(document.getElementById(id));});},180);});
  window.addEventListener("beforeunload",clearAssets);
  select(document.getElementById("windowPick"),"hours",state.hours);select(document.getElementById("localePick"),"locale",state.locale);select(document.getElementById("atlasSort"),"sort",state.sort);setView(state.view,false);
  var saved=sessionRead("backyardApiToken");if(saved){tokenInput.value=saved;refresh();}
}());
