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
  function storySlides(summary,fact){var slides=[];if(summary)slides.push({title:"Over deze vogel",text:summary});if(fact)slides.push({title:"Wist je dat?",text:fact});return slides;}
  function steppedIndex(index,step,length){return length?((index+step)%length+length)%length:0;}
  function stateVisibility(state){return{loading:state==="loading",content:state==="loaded",error:state==="error"};}
  function isCurrentRequest(expected,current){return expected===current;}
  function present(data,locale){
    data=data||{};var encyclopedia=data.encyclopedia||{},observations=data.observations||{},audio=data.audio||null,waarneming=data.waarneming||{},assets=(data.generator&&data.generator.assets)||{};
    return{poses:availablePoses(assets),summary:encyclopedia.summary_nl||null,fact:encyclopedia.fact_nl||null,wikipedia:encyclopedia.wikipedia_nl_url?{url:encyclopedia.wikipedia_nl_url,language:"nl"}:encyclopedia.wikipedia_en_url?{url:encyclopedia.wikipedia_en_url,language:"en"}:null,waarnemingUrl:waarneming.url||null,audio:audio&&audio.availability&&audio.url?audio:null,total:Number(observations.total||0),confidence:formatConfidence(observations.highest_confidence,locale),first:formatDateTime(observations.first_observed_at,locale),last:formatDateTime(observations.last_observed_at,locale)};
  }
  function create(options){
    var modal=document.getElementById("speciesDetail"),body=document.getElementById("detailBody"),loading=document.getElementById("detailLoading"),errorBox=document.getElementById("detailError"),story=document.getElementById("detailStory"),cache=new Map(),opener=null,current=null,audio=null,audioObjectUrl=null,request=0,controller=null,slides=[],slideIndex=0,touchStart=null;
    function text(id,value){document.getElementById(id).textContent=value||"";}
    function show(id,visible){document.getElementById(id).hidden=!visible;}
    function setState(next,message){var visibility=stateVisibility(next);modal.dataset.state=next;["idle","loading","loaded","error"].forEach(function(name){modal.classList.toggle("is-"+name,name===next);});loading.hidden=!visibility.loading;body.hidden=!visibility.content;errorBox.hidden=!visibility.error;errorBox.textContent=visibility.error?(message||"Soortinformatie kon niet worden geladen."):"";}
    function audioLabel(icon,label){var button=document.getElementById("detailAudio");button.replaceChildren();var span=document.createElement("span");span.textContent=icon;button.append(span,document.createTextNode(" "+label));}
    function stopAudio(){if(audio){audio.pause();audio.src="";audio=null;}if(audioObjectUrl){URL.revokeObjectURL(audioObjectUrl);audioObjectUrl=null;}var button=document.getElementById("detailAudio");button.setAttribute("aria-pressed","false");button.disabled=false;audioLabel("▶","Beluister deze waarneming");}
    function paint(pose){
      var artwork=document.getElementById("detailArtwork"),image=document.getElementById("detailImage"),assets=current&&current.generator&&current.generator.assets||{},asset=assets[pose];
      document.querySelectorAll("#detailPoseToggle button").forEach(function(button){button.setAttribute("aria-current",button.dataset.pose===pose?"true":"false");});
      image.removeAttribute("src");image.classList.remove("ready");artwork.dataset.artState=asset&&asset.url?"loading":"missing";
      if(asset&&asset.url)options.loadAsset(asset.url,image).then(function(){artwork.dataset.artState="ready";}).catch(function(){image.removeAttribute("src");artwork.dataset.artState="missing";});
    }
    function addObservation(dl,label,value){if(value==null||value==="")return;var row=document.createElement("div"),dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=label;dd.textContent=value;row.append(dt,dd);dl.appendChild(row);}
    function showSlide(index){if(!slides.length)return;slideIndex=steppedIndex(index,0,slides.length);var slide=slides[slideIndex],paragraph=document.getElementById("detailStoryText");text("detailStoryTitle",slide.title);text("detailStoryText",slide.text);text("detailStoryCounter",slides.length>1?(slideIndex+1)+" / "+slides.length:"");paragraph.classList.remove("is-entering");void paragraph.offsetWidth;paragraph.classList.add("is-entering");document.querySelectorAll("#detailStoryDots button").forEach(function(button,i){button.setAttribute("aria-current",i===slideIndex?"true":"false");});}
    function renderStory(summary,fact){slides=storySlides(summary,fact);slideIndex=0;story.hidden=!slides.length;var nav=document.getElementById("detailStoryNav"),dots=document.getElementById("detailStoryDots");nav.hidden=slides.length<2;dots.replaceChildren();slides.forEach(function(slide,index){var button=document.createElement("button");button.type="button";button.setAttribute("aria-label",slide.title);button.addEventListener("click",function(){showSlide(index);});dots.appendChild(button);});if(slides.length)showSlide(0);}
    function render(data){
      current=data;var locale=options.locale(),vm=present(data,locale==="nl"?"nl-NL":locale),identity=data.identity||{},waarneming=data.waarneming||{},obs=data.observations||{},poses=vm.poses;
      text("detailCommon",identity.common_name||identity.scientific_name);text("detailScientific",identity.scientific_name);var taxonomy=[familyLabel(identity.family),identity.authority].filter(Boolean).join(" · ");text("detailTaxonomy",taxonomy);show("detailTaxonomy",!!taxonomy);
      var badges=document.getElementById("detailBadges");badges.replaceChildren();[waarneming.status,waarneming.rarity,waarneming.obscurity].filter(Boolean).forEach(function(value){var span=document.createElement("span");span.textContent=value;badges.appendChild(span);});badges.hidden=!badges.children.length;
      renderStory(vm.summary,vm.fact);
      var dl=document.getElementById("detailObservations");dl.replaceChildren();addObservation(dl,"Gehoord",vm.total+"×");addObservation(dl,"Vandaag",obs.today==null?null:Number(obs.today)+"×");addObservation(dl,"Afgelopen 7 dagen",obs.last_7_days==null?null:Number(obs.last_7_days)+"×");addObservation(dl,"Eerste waarneming",vm.first);addObservation(dl,"Laatste waarneming",vm.last);addObservation(dl,"Beste herkenning",vm.confidence);
      var toggle=document.getElementById("detailPoseToggle");toggle.hidden=poses.length<2;toggle.querySelectorAll("button").forEach(function(button){button.hidden=poses.indexOf(button.dataset.pose)<0;});paint(poses[0]||null);
      show("detailAudioSection",!!vm.audio);if(vm.audio)text("detailAudioMeta",[formatDateTime(vm.audio.timestamp,locale==="nl"?"nl-NL":locale),formatConfidence(vm.audio.confidence,locale==="nl"?"nl-NL":locale)].filter(Boolean).join(" · "));
      var links=document.getElementById("detailLinks");links.replaceChildren();function link(label,url){var a=document.createElement("a");a.href=url;a.target="_blank";a.rel="noopener noreferrer";a.textContent=label;links.appendChild(a);}if(vm.wikipedia)link(vm.wikipedia.language==="en"?"Meer op Wikipedia (Engels)":"Meer op Wikipedia",vm.wikipedia.url);if(vm.waarnemingUrl)link("Bekijk op Waarneming.nl",vm.waarnemingUrl);show("detailLinks",links.children.length>0);
      setState("loaded");document.querySelector(".postcard-close").focus();
    }
    function open(scientificName,source){
      opener=source||document.activeElement;if(controller)controller.abort();controller=typeof AbortController!=="undefined"?new AbortController():null;stopAudio();current=null;setState("loading");modal.setAttribute("aria-hidden","false");requestAnimationFrame(function(){modal.classList.add("is-open");});document.body.classList.add("postcard-open");
      var key=options.locale()+"|"+scientificName,seq=++request,promise=cache.has(key)?Promise.resolve(cache.get(key)):options.fetchJson(detailUrl(scientificName,options.locale()),controller?{signal:controller.signal}:undefined).then(function(data){cache.set(key,data);return data;});
      return promise.then(function(data){if(isCurrentRequest(seq,request))render(data);return data;}).catch(function(error){if(!isCurrentRequest(seq,request)||error&&error.name==="AbortError")return;current=null;setState("error",error&&error.message);});
    }
    function close(){request++;if(controller)controller.abort();controller=null;stopAudio();current=null;setState("idle");modal.classList.remove("is-open");modal.setAttribute("aria-hidden","true");document.body.classList.remove("postcard-open");if(opener&&document.contains(opener))opener.focus();opener=null;}
    modal.addEventListener("click",function(event){if(event.target.closest("[data-detail-close]"))close();var pose=event.target.closest("button[data-pose]");if(pose)paint(pose.dataset.pose);});
    document.getElementById("detailStoryNav").addEventListener("click",function(event){var button=event.target.closest("button[data-story-step]");if(button)showSlide(steppedIndex(slideIndex,+button.dataset.storyStep,slides.length));});
    story.addEventListener("keydown",function(event){if(event.key==="ArrowLeft"||event.key==="ArrowRight"){event.preventDefault();showSlide(steppedIndex(slideIndex,event.key==="ArrowLeft"?-1:1,slides.length));}});
    story.addEventListener("touchstart",function(event){touchStart=event.changedTouches&&event.changedTouches[0]?event.changedTouches[0].clientX:null;},{passive:true});story.addEventListener("touchend",function(event){if(touchStart==null||!event.changedTouches||!event.changedTouches[0])return;var delta=event.changedTouches[0].clientX-touchStart;touchStart=null;if(Math.abs(delta)>42)showSlide(steppedIndex(slideIndex,delta<0?1:-1,slides.length));},{passive:true});
    document.addEventListener("keydown",function(event){if(event.key==="Escape"&&modal.getAttribute("aria-hidden")==="false")close();});
    document.getElementById("detailAudio").addEventListener("click",function(){var button=this;if(audio){if(audio.paused){audio.play();button.setAttribute("aria-pressed","true");audioLabel("❚❚","Pauzeer deze waarneming");}else{audio.pause();button.setAttribute("aria-pressed","false");audioLabel("▶","Beluister deze waarneming");}return;}button.disabled=true;options.fetchBlob(current.audio.url).then(function(blob){audioObjectUrl=URL.createObjectURL(blob);audio=new Audio(audioObjectUrl);audio.addEventListener("ended",stopAudio);return audio.play();}).then(function(){button.disabled=false;button.setAttribute("aria-pressed","true");audioLabel("❚❚","Pauzeer deze waarneming");}).catch(function(){button.disabled=false;audioLabel("!","Opname niet beschikbaar");});});
    return{open:open,close:close,clearCache:function(){cache.clear();}};
  }
  return{detailUrl:detailUrl,availablePoses:availablePoses,familyLabel:familyLabel,formatConfidence:formatConfidence,formatDateTime:formatDateTime,storySlides:storySlides,steppedIndex:steppedIndex,stateVisibility:stateVisibility,isCurrentRequest:isCurrentRequest,present:present,create:create};
}));
