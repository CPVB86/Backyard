/* Species postcard adapted from the original AvianVisitors interaction; data remains owned by Backyard. */
(function(root,factory){
  "use strict";
  var api=factory();
  if(typeof module==="object"&&module.exports)module.exports=api;
  if(root)root.AvianDetail=api;
}(typeof window!=="undefined"?window:null,function(){
  "use strict";
  function detailUrl(scientificName,locale){return "/api/species/bird/"+encodeURIComponent(scientificName)+"?locale="+encodeURIComponent(locale||"nl");}
  function availablePoses(assets){return ["perched","flight"].filter(function(pose){return !!(assets&&assets[pose]&&assets[pose].url);});}
  function familyLabel(value){var match=String(value||"").match(/^([^()]+?)\s*\(([^()]+)\)$/);return match?match[2].trim()+" · "+match[1].trim():String(value||"");}
  function formatConfidence(value,locale){return value==null?null:new Intl.NumberFormat(locale||"nl-NL",{style:"percent",minimumFractionDigits:1,maximumFractionDigits:1}).format(value);}
  function formatDateTime(value,locale){if(!value)return null;var date=new Date(value);if(isNaN(date.getTime()))return null;return new Intl.DateTimeFormat(locale||"nl-NL",{dateStyle:"long",timeStyle:"short",timeZone:"Europe/Amsterdam"}).format(date);}
  function present(data,locale){
    data=data||{};var encyclopedia=data.encyclopedia||{},observations=data.observations||{},audio=data.audio||null,waarneming=data.waarneming||{},assets=(data.generator&&data.generator.assets)||{};
    return{poses:availablePoses(assets),summary:encyclopedia.summary_nl||null,fact:encyclopedia.fact_nl||null,wikipedia:encyclopedia.wikipedia_nl_url?{url:encyclopedia.wikipedia_nl_url,language:"nl"}:encyclopedia.wikipedia_en_url?{url:encyclopedia.wikipedia_en_url,language:"en"}:null,waarnemingUrl:waarneming.url||null,audio:audio&&audio.availability&&audio.url?audio:null,total:Number(observations.total||0),confidence:formatConfidence(observations.highest_confidence,locale),first:formatDateTime(observations.first_observed_at,locale),last:formatDateTime(observations.last_observed_at,locale)};
  }
  function create(options){
    var modal=document.getElementById("speciesDetail"),body=document.getElementById("detailBody"),loading=document.getElementById("detailLoading"),cache=new Map(),opener=null,current=null,audio=null,audioObjectUrl=null,request=0;
    function text(id,value){document.getElementById(id).textContent=value||"";}
    function show(id,visible){document.getElementById(id).hidden=!visible;}
    function audioLabel(icon,label){var button=document.getElementById("detailAudio");button.replaceChildren();var span=document.createElement("span");span.textContent=icon;button.append(span,document.createTextNode(" "+label));}
    function stopAudio(){if(audio){audio.pause();audio.src="";audio=null;}if(audioObjectUrl){URL.revokeObjectURL(audioObjectUrl);audioObjectUrl=null;}var button=document.getElementById("detailAudio");button.setAttribute("aria-pressed","false");button.disabled=false;audioLabel("▶","Beluister deze waarneming");}
    function paint(pose){
      var artwork=document.getElementById("detailArtwork"),image=document.getElementById("detailImage"),assets=current&&current.generator&&current.generator.assets||{},asset=assets[pose];
      document.querySelectorAll("#detailPoseToggle button").forEach(function(button){button.setAttribute("aria-current",button.dataset.pose===pose?"true":"false");});
      image.removeAttribute("src");image.classList.remove("ready");artwork.dataset.artState=asset&&asset.url?"loading":"missing";
      if(asset&&asset.url)options.loadAsset(asset.url,image).then(function(){artwork.dataset.artState="ready";}).catch(function(){image.removeAttribute("src");artwork.dataset.artState="missing";});
    }
    function addObservation(dl,label,value){if(value==null||value==="")return;var row=document.createElement("div"),dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=label;dd.textContent=value;row.append(dt,dd);dl.appendChild(row);}
    function render(data){
      current=data;var locale=options.locale(),vm=present(data,locale==="nl"?"nl-NL":locale),identity=data.identity||{},waarneming=data.waarneming||{},obs=data.observations||{},poses=vm.poses;
      text("detailCommon",identity.common_name||identity.scientific_name);text("detailScientific",identity.scientific_name);text("detailTaxonomy",[familyLabel(identity.family),identity.authority].filter(Boolean).join(" · "));
      var badges=document.getElementById("detailBadges");badges.replaceChildren();[waarneming.status,waarneming.rarity,waarneming.obscurity].filter(Boolean).forEach(function(value){var span=document.createElement("span");span.textContent=value;badges.appendChild(span);});
      text("detailSummary",vm.summary||"Voor deze soort is nog geen encyclopedietekst beschikbaar.");show("detailFactSection",!!vm.fact);text("detailFact",vm.fact);
      var dl=document.getElementById("detailObservations");dl.replaceChildren();addObservation(dl,"Gehoord",vm.total+"×");addObservation(dl,"Vandaag",Number(obs.today||0)+"×");addObservation(dl,"Afgelopen 7 dagen",Number(obs.last_7_days||0)+"×");addObservation(dl,"Eerste waarneming",vm.first);addObservation(dl,"Laatste waarneming",vm.last);addObservation(dl,"Beste herkenning",vm.confidence);
      var toggle=document.getElementById("detailPoseToggle");toggle.hidden=poses.length<2;toggle.querySelectorAll("button").forEach(function(button){button.hidden=poses.indexOf(button.dataset.pose)<0;});paint(poses[0]||null);
      show("detailAudioSection",!!vm.audio);if(vm.audio)text("detailAudioMeta",[formatDateTime(vm.audio.timestamp,locale==="nl"?"nl-NL":locale),formatConfidence(vm.audio.confidence,locale==="nl"?"nl-NL":locale)].filter(Boolean).join(" · "));
      var links=document.getElementById("detailLinks");links.replaceChildren();function link(label,url){var a=document.createElement("a");a.href=url;a.target="_blank";a.rel="noopener noreferrer";a.textContent=label;links.appendChild(a);}if(vm.wikipedia)link(vm.wikipedia.language==="en"?"Meer op Wikipedia (Engels)":"Meer op Wikipedia",vm.wikipedia.url);if(vm.waarnemingUrl)link("Bekijk op Waarneming.nl",vm.waarnemingUrl);show("detailLinks",links.children.length>0);
      loading.hidden=true;body.hidden=false;document.querySelector(".postcard-close").focus();
    }
    function open(scientificName,source){
      opener=source||document.activeElement;stopAudio();body.hidden=true;loading.hidden=false;loading.textContent="Soortinformatie laden…";modal.setAttribute("aria-hidden","false");requestAnimationFrame(function(){modal.classList.add("is-open");});document.body.classList.add("postcard-open");
      var key=options.locale()+"|"+scientificName,seq=++request,promise=cache.has(key)?Promise.resolve(cache.get(key)):options.fetchJson(detailUrl(scientificName,options.locale())).then(function(data){cache.set(key,data);return data;});
      return promise.then(function(data){if(seq===request)render(data);return data;}).catch(function(error){if(seq!==request)return;loading.textContent=error.message||"Soortinformatie kon niet worden geladen.";});
    }
    function close(){request++;stopAudio();modal.classList.remove("is-open");modal.setAttribute("aria-hidden","true");document.body.classList.remove("postcard-open");if(opener&&document.contains(opener))opener.focus();opener=null;}
    modal.addEventListener("click",function(event){if(event.target.closest("[data-detail-close]"))close();var pose=event.target.closest("button[data-pose]");if(pose)paint(pose.dataset.pose);});
    document.addEventListener("keydown",function(event){if(event.key==="Escape"&&modal.getAttribute("aria-hidden")==="false")close();});
    document.getElementById("detailAudio").addEventListener("click",function(){var button=this;if(audio){if(audio.paused){audio.play();button.setAttribute("aria-pressed","true");audioLabel("❚❚","Pauzeer deze waarneming");}else{audio.pause();button.setAttribute("aria-pressed","false");audioLabel("▶","Beluister deze waarneming");}return;}button.disabled=true;options.fetchBlob(current.audio.url).then(function(blob){audioObjectUrl=URL.createObjectURL(blob);audio=new Audio(audioObjectUrl);audio.addEventListener("ended",stopAudio);return audio.play();}).then(function(){button.disabled=false;button.setAttribute("aria-pressed","true");audioLabel("❚❚","Pauzeer deze waarneming");}).catch(function(){button.disabled=false;audioLabel("!","Opname niet beschikbaar");});});
    return{open:open,close:close,clearCache:function(){cache.clear();}};
  }
  return{detailUrl:detailUrl,availablePoses:availablePoses,familyLabel:familyLabel,formatConfidence:formatConfidence,formatDateTime:formatDateTime,present:present,create:create};
}));
