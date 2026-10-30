#import "@preview/cetz:0.5.2": canvas, draw
#import "map.typ": *
#import "technical.typ": *
#import "pruning/pruning-movie.typ": figure as trace-figure

#set page(width:276mm,height:128mm,margin:0mm,fill:white)
#set text(font:"Arial",size:14pt,fill:black)
#let coast=json("data/map-coast-display.json")
#let harbor=json("data/map-charleston.json")
#let frame=json("data/frame.json")
#let result=json("data/result.json")

#let big(p,value,caption,col:black,size:37pt) = {
  draw.content(p,text(size:size,weight:"bold",fill:col)[#value])
  draw.content((p.at(0),p.at(1) - 12),text(size:13pt)[#caption])
}

#let empty-capture(p,w,h,heading,detail) = {
  import draw: *
  let (x,y)=p
  rect(p,(x+w,y+h),fill:white,stroke:(paint:rgb("#777777"),thickness:0.7pt,dash:"dashed"))
  content((x+w/2,y+h/2+5),text(size:17pt)[#heading])
  content((x+w/2,y+h/2-6),text(size:11pt,fill:rgb("#555555"))[#detail])
}

#let scene-polar(reveal:1) = canvas(length:1mm,{
  import draw: *
  rect((0,0),(276,128),stroke:none)
  basemap(harbor,origin:(0,10),w:137,h:114,grid-step:5000,title:[Charleston])
  line((143,65),(160,65),mark:(end:"stealth",fill:black),stroke:1.2pt+black)
  polar(frame.measurements,origin:(218,66),r:42,reveal:reveal)
  content((218,9),text(size:13pt)[64 aids + 6 vessel reports])
  legend(origin:(4,3))
})

#let result-plot() = canvas(length:1mm,{
  import draw: *
  rect((0,0),(276,128),stroke:none)
  let xy=p=>(71+(p.at(0) - result.estimate.at(0))*3.4,61+(p.at(1) - result.estimate.at(1))*3.4)
  for tick in range(-15,16,step:5) {
    line((20,61+tick*3.4),(122,61+tick*3.4),stroke:0.3pt+rgb("#b7b7b7"))
    line((71+tick*3.4,10),(71+tick*3.4,112),stroke:0.3pt+rgb("#b7b7b7"))
    content((15,61+tick*3.4),text(size:10pt)[#tick],anchor:"east")
    content((71+tick*3.4,6),text(size:10pt)[#tick])
  }
  for p in result.terminals {
    let c=xy(p.xy)
    if p.status==0 {circle(c,radius:0.65,fill:green,stroke:none)}
    else if p.status==1 {
      line((c.at(0) - 0.6,c.at(1) - 0.6),(c.at(0)+0.6,c.at(1)+0.6),stroke:0.5pt+red)
      line((c.at(0) - 0.6,c.at(1)+0.6),(c.at(0)+0.6,c.at(1) - 0.6),stroke:0.5pt+red)
    } else {circle(c,radius:0.7,fill:white,stroke:0.5pt+purple)}
  }
  symbol(xy(result.truth),kind:"truth",r:1.5)
  symbol(xy(result.estimate),kind:"estimate",r:1.7)
  content((73,124),text(size:14pt)[Offset from selected estimate · meters])
  big((210,104),[148],[Consistent centers],col:green,size:29pt)
  big((210,69),[67],[Inconsistent centers],col:red,size:29pt)
  big((210,34),[182],[Unresolved centers],col:purple,size:29pt)
  content((210,7),text(size:11pt)[× Truth   ◆ Selected estimate])
})

#let sensor-table() = canvas(length:1mm,{
  import draw: *
  rect((0,0),(276,128),stroke:none)
  let rows=(("Coverage / radius","360° / 10 km"),("Angular grouping","0.1°"),("Gaussian σ / hard bound","1/6° / ±0.5°"),("Detection probability","0.9"),("Type errors / bearing outliers","0 / 0"),("Extra vessels","Poisson mean 1 · realized 1"))
  for (i,row) in rows.enumerate() {
    let y=112-i*18
    content((12,y),text(size:17pt)[#row.at(0)],anchor:"west")
    content((264,y),text(size:18pt,fill:blue)[#row.at(1)],anchor:"east")
    line((12,y - 7),(264,y - 7),stroke:0.35pt+rgb("#bbbbbb"))
  }
})

#let envelope-proof() = canvas(length:1mm,{
  import draw: *
  rect((0,0),(276,128),stroke:none)
  content((138,107),text(size:28pt)[$E_("true") (C) subset.eq E_("possible") (C)$])
  content((138,83),text(size:17pt)[Possible edges cover every physical explanation within the cell.])
  line((138,70),(138,57),stroke:1.1pt+black,mark:(end:"stealth",fill:black))
  content((138,41),text(size:25pt)[$nu(E_("possible") (C)) < 64 => C " is rejected"$])
  content((138,14),text(size:17pt)[Unknown bounds, identity or capacity → retain.])
})

#let exact-arithmetic() = canvas(length:1mm,{
  import draw: *
  rect((0,0),(276,128),stroke:none)
  content((138,112),text(size:17pt)[Calculated work counts])
  content((138,91),text(size:25pt)[$1 644 677 times 2 388 460 = 3 928 245 227 420$])
  content((138,68),text(size:18pt)[1 m rectangular grid positions])
  content((138,41),text(size:23pt)[$39 435 times 4^14 = 10 585 752 207 360$])
  content((138,19),text(size:17pt)[Terminal cells if every root is refined without pruning])
  content((138,3),text(size:12pt)[14,114,336,263,335 total tree nodes · no measured exhaustive runtime])
})

#let epochs() = canvas(length:1mm,{
  import draw: *
  rect((0,0),(276,128),stroke:none)
  for (y,date,what,col) in ((104,"15 July 2023","AIS snapshot · 12:00–12:01 UTC",orange),(68,"24 October 2026","ENC chart-point retrieval",blue),(32,"26 October 2026","Saved shoreline export",black)) {
    content((70,y),text(size:23pt,fill:col)[#date])
    content((202,y),text(size:17pt)[#what])
  }
  content((138,5),text(size:13pt)[Mixed epochs · archived geographic test world])
})

#let timings() = canvas(length:1mm,{
  import draw: *
  rect((0,0),(276,128),stroke:none)
  for (y,value,t,col) in ((105,7.3882154,"Saved-input loading",rgb("#7f7f7f")),(67,8.4619355,"Search API",blue),(29,23.4491887,"Diagnostic supervisor",black)) {
    content((8,y),text(size:16pt)[#t],anchor:"west")
    rect((101,y - 5),(101+value*6,y+5),fill:col,stroke:none)
    content((106+value*6,y),text(size:17pt)[#calc.round(value,digits:2) s],anchor:"west")
  }
  content((138,4),text(size:13pt)[Different scopes · saved cache was read, not rebuilt])
})


#let graphic(id) = {
  if id == "01" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      basemap(harbor,origin:(0,12),w:204,h:113,grid-step:5000,point-radius:0.9)
      basemap(coast,origin:(210,31),w:64,h:92,points:false,observer:true)
      label((242,18),[Charleston · B17],size:13pt)
      legend(origin:(8,4))
    })
  } else if id == "02" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      basemap(coast,origin:(6,9),w:100,h:115,points:false,title:[Eastern Seaboard])
      basemap(harbor,origin:(119,9),w:155,h:115,title:[Charleston],grid-step:5000)
      line((109,65),(119,65),mark:(end:"stealth",fill:black),stroke:1pt+black)
    })
  } else if id == "03" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      basemap(harbor,origin:(0,5),w:182,h:120,grid-step:5000)
      let tr=mapper(harbor,(0,5),182,120)
      let o=harbor.observer
      let q=frame.at("target_xy",default:harbor.aids.first())
      let p=tr.point
      line(p(o),p(q),stroke:1.5pt+blue,mark:(end:"stealth",fill:blue))
      for k in (-0.1,0.1) {
        let v=(o.at(0)+k*(q.at(0) - o.at(0)),o.at(1)+k*(q.at(1) - o.at(1)))
        symbol(p(v),kind:"estimate",r:1.8)
      }
      big((229,88),[±10%],[Range sensitivity])
      let d=calc.sqrt(calc.pow(q.at(0) - o.at(0),2)+calc.pow(q.at(1) - o.at(1),2))*0.1
      big((229,57),[±#calc.round(d) m],[Calculated displacement],col:purple,size:31pt)
      line((190,20),(268,20),stroke:0.8pt+black)
      for (x,t,kind) in ((196,[−#calc.round(d)],"estimate"),(229,[0],"observer"),(262,[+#calc.round(d)],"estimate")) {
        symbol((x,20),kind:kind,r:1.5)
        content((x,10),text(size:11pt)[#t])
      }
      content((229,32),text(size:11pt)[Along the observed ray · meters])
      content((135,3),text(size:10pt)[Calculation on one actual mapped-object ray])
    })
  } else if id == "04" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      basemap(harbor,origin:(0,5),w:170,h:120,grid-step:5000)
      let tr=mapper(harbor,(0,5),170,120)
      let p=tr.point
      let o=harbor.observer
      let q=frame.at("target_xy",default:harbor.aids.first())
      let demo=json("data/heading-demo.json")
      line(p(o),p(q),stroke:1.3pt+blue,mark:(end:"stealth",fill:blue))
      line(p(o),p(demo.north),stroke:1.2pt+black,mark:(end:"stealth",fill:black))
      line(p(o),p(demo.heading),stroke:1.2pt+purple,mark:(end:"stealth",fill:purple))
      label(p(demo.north),[True north],size:11pt,anchor:"south")
      label(p(demo.heading),[Heading],size:11pt,anchor:"south-west",fill:purple)
      let c=p(o)
      let angles=json("data/heading-demo.json").grid_angles_deg
      for (start,end,radius,col,body) in ((angles.north,angles.heading,12,purple,[$psi$]),(angles.heading,angles.target,18,blue,[$alpha$])) {
        line(..range(0,31).map(i=>{
          let a=(start+(end - start)*i/30)*1deg
          (c.at(0)+radius*calc.cos(a),c.at(1)+radius*calc.sin(a))
        }),stroke:0.9pt+col,mark:(end:"stealth",fill:col))
        let a=(start+end)/2*1deg
        label((c.at(0)+(radius+3)*calc.cos(a),c.at(1)+(radius+3)*calc.sin(a)),body,size:13pt,fill:col)
      }
      content((224,83),text(size:28pt)[$beta=op("wrap")(psi+alpha)$])
      content((224,54),text(size:16pt)[$psi$ · known heading])
      content((224,40),text(size:16pt)[$alpha$ · relative bearing])
      content((224,26),text(size:16pt)[$beta$ · global bearing])
      content((224,8),text(size:11pt)[Heading-conversion example · calculated $psi=30degree$])
    })
  } else if id == "05" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      polar(frame.measurements,origin:(64,66),r:44,mode:"physical_aid")
      basemap(harbor,origin:(133,10),w:140,h:114,grid-step:5000)
      label((64,7),[64 chart-aid bearings],size:16pt)
    })
  } else if id == "06" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      basemap(coast,origin:(0,37),w:58,h:83,points:true,observer:false,labels:false,point-radius:0.35)
      polar(frame.measurements,origin:(105,76),r:27)
      basemap(harbor,origin:(185,37),w:87,h:83,points:true,labels:false)
      line((60,77),(72,77),mark:(end:"stealth",fill:black),stroke:1pt+black)
      line((140,77),(153,77),mark:(end:"stealth",fill:black),stroke:1pt+black)
      content((169,77),text(size:16pt)[Search])
      line((185,77),(193,77),mark:(end:"stealth",fill:black),stroke:1pt+black)
      for (x,t) in ((29,"Catalog"),(105,"Observations"),(232,"Location")) {
        content((x,21),text(size:18pt)[#t])
      }
      content((138,4),text(size:11pt)[Current localization objective · chart-aid bearings])
    })
  } else if id == "07" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      basemap(harbor,origin:(0,33),w:127,h:88,points:false,observer:false,labels:false)
      let tr=mapper(harbor,(0,33),127,88)
      for p in harbor.aids {symbol((tr.point)(p),r:0.8)}
      basemap(harbor,origin:(149,33),w:127,h:88,points:false,observer:false,labels:false)
      let tr=mapper(harbor,(149,33),127,88)
      for p in harbor.ais {symbol((tr.point)(p),kind:"ais",r:1.1)}
      content((63,20),text(size:22pt)[NOAA ENC])
      content((63,8),text(size:12pt)[Chart features and shoreline])
      content((213,20),text(size:22pt)[Archived AIS])
      content((213,8),text(size:12pt)[NOAA vessel-position snapshot])
    })
  } else if id == "08" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      basemap(coast,origin:(0,5),w:137,h:120,points:true,point-radius:0.18,observer:false)
      big((207,93),[32,168],[Canonical chart locations],col:blue,size:35pt)
      big((207,43),[2,268],[AIS-source records],col:orange,size:35pt)
    })
  } else if id == "09" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      basemap(harbor,origin:(0,10),w:225,h:115,grid-step:5000,point-radius:0.8)
      content((249,92),text(size:25pt,fill:blue)[#(harbor.aids.len()+harbor.unknown_objects.len())])
      content((249,80),text(size:12pt)[Chart locations])
      content((249,52),text(size:25pt,fill:orange)[#harbor.ais.len()])
      content((249,40),text(size:12pt)[AIS records])
      legend(origin:(8,3))
    })
  } else if id == "10" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      let local=json("data/map-canonical.json")
      basemap(local,origin:(0,8),w:150,h:114,points:true,grid-step:200)
      let tr=mapper(local,(0,8),150,114)
      let p=json("data/canonical-triple.json").example.xy
      circle((tr.point)(p),radius:4,fill:none,stroke:1.2pt+garnet)
      big((215,99),[3 → 1],[Co-located source records],col:blue,size:34pt)
      content((215,60),text(size:17pt)[Beacon · light · daymark])
      content((215,44),text(size:13pt)[32.7731847°, −79.9054558°])
      content((215,17),text(size:17pt)[64,400 → 32,168])
      content((215,5),text(size:11pt)[Raw rows → canonical chart locations])
    })
  } else if id == "11" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      basemap(coast,origin:(0,5),w:124,h:120,points:false,observer:false)
      let tr=mapper(coast,(0,5),124,120)
      for p in coast.ais {symbol((tr.point)(p),kind:"ais",r:0.35)}
      content((204,101),text(size:26pt)[15 July 2023])
      line((144,62),(263,62),stroke:1.5pt+black)
      for (x,t) in ((144,"12:00"),(203.5,"12:00:30"),(263,"12:01")) {
        line((x,59),(x,65),stroke:1pt+black)
        content((x,52),text(size:12pt)[#t])
      }
      content((205,29),text(size:18pt)[2,268 retained records])
      content((205,15),text(size:12pt)[Nearest report to midpoint per identity · UTC])
    })
  } else if id == "12" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      basemap(coast,origin:(0,4),w:165,h:121,points:false,observer:false)
      let tr=mapper(coast,(0,4),165,121)
      for site in coast.observers {
        symbol((tr.point)(site.xy),kind:"observer",r:0.6)
      }
      for (i,site) in coast.observers.sorted(key:s=>s.xy.at(1)).enumerate() {
        let y=9+i*4.65
        line((21,y),(tr.point)(site.xy),stroke:0.3pt+black)
        label((20,y),site.id,size:9pt,anchor:"east")
      }
      big((220,88),[24],[Test locations],size:52pt)
      content((220,42),text(size:16pt)[Charleston · B17])
      content((220,25),text(size:12pt)[Completed coast-wide search example])
    })
  } else if id == "13" { scene-polar() }
  else if id == "14" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      basemap(harbor,origin:(0,7),w:180,h:116,points:true,grid-step:5000,ranges:(10000,))
      big((228,103),[360°],[Field of view],size:31pt)
      big((228,66),[10 km],[Visibility radius],size:31pt)
      content((228,27),text(size:19pt)[0.1° grouping])
      content((228,10),text(size:19pt)[±0.5° error bound])
    })
  } else if id == "15" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      for (i,scene) in json("data/range-scenes.json").enumerate() {
        let radius=scene.radius_m
        let x=i*69
        let m=harbor
        m.insert("aids",scene.aids)
        m.insert("ais",scene.ais)
        m.insert("unknown_objects",())
        basemap(m,origin:(x,36),w:67,h:88,points:true,labels:false,ranges:(radius,),point-radius:0.65)
        content((x+33,26),text(size:21pt)[#(radius/1000) km])
        content((x+33,14),text(size:12pt)[#scene.visible_count / #scene.in_range_count])
      }
      content((138,4),text(size:10pt)[Visible / in-range physical objects · saved line-of-sight classifications])
    })
  } else if id == "16" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      basemap(harbor,origin:(0,10),w:194,h:115,grid-step:5000)
      let tr=mapper(harbor,(0,10),194,115)
      for item in frame.at("los_examples",default:()) {
        let col=if item.at("blocked",default:false){red}else{green}
        line((tr.point)(harbor.observer),(tr.point)(item.xy),stroke:1.6pt+col)
        symbol((tr.point)(item.xy),r:1.3)
      }
      content((235,88),text(size:23pt,fill:green)[Visible])
      content((235,70),text(size:14pt)[2.750 km])
      content((235,40),text(size:23pt,fill:red)[Land blocked])
      content((235,22),text(size:14pt)[7.372 km])
    })
  } else if id == "17" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      let local=json("data/map-merge.json")
      basemap(local,origin:(0,8),w:177,h:117,grid-step:1000)
      let tr=mapper(local,(0,8),177,117)
      for p in frame.at("merged_xy",default:()) {
        line((tr.point)(harbor.observer),(tr.point)(p),stroke:1pt+purple)
        symbol((tr.point)(p),r:1.1)
      }
      let near=(tr.point)(frame.merged_xy.first())
      let far=(tr.point)(frame.merged_xy.last())
      circle(near,radius:2,stroke:1.2pt+purple,fill:none)
      label((near.at(0)+5,near.at(1)),[Representative · 2.036 km],size:11pt,anchor:"west")
      label((far.at(0) - 4,far.at(1)),[4.433 km],size:11pt,anchor:"east")
      big((228,108),[80 → 79],[Visible objects → angular groups],col:purple,size:30pt)
      content((228,79),text(size:13pt)[Bearing · magnified])
      line((187,48),(267,48),stroke:0.6pt+black,mark:(end:"stealth",fill:black))
      for (i,obj) in frame.merged_objects.enumerate() {
        let x=187+(obj.true_bearing_deg - 343.55)*400
        line((x,45),(x,63),stroke:1.1pt+purple)
        content((x,if i==0 {69}else{39}),text(size:11pt)[#calc.round(obj.true_bearing_deg,digits:3)°])
      }
      content((228,24),text(size:17pt)[Δβ = 0.09147°])
      content((228,9),text(size:12pt)[0.1° grouping threshold])
    })
  } else if id == "18" {
    let histogram=json("data/noise-histogram.json")
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      line((20,20),(185,20),stroke:0.8pt+black,mark:(end:"stealth",fill:black))
      line((20,20),(20,104),stroke:0.8pt+black,mark:(end:"stealth",fill:black))
      for bin in histogram.bins {
        let x=20+(bin.left+0.5)*165
        rect((x+0.4,20),(x+16.1,20+bin.count*3.2),fill:blue,stroke:0.3pt+white)
      }
      for n in range(0,26,step:5) {
        content((16,20+n*3.2),text(size:10pt)[#n],anchor:"east")
      }
      for x in (-0.5,0,0.5) {
        let px=20+(x+0.5)*165
        line((px,19),(px,21),stroke:0.6pt+black)
        content((px,13),text(size:12pt)[#x°])
      }
      content((98,3),text(size:10pt)[Recovered errors · 69 simulated physical-object reports])
      big((234,87),[1/6°],[Underlying standard deviation],size:30pt)
      big((234,40),[±0.5°],[Hard bearing-error bound],size:30pt)
    })
  } else if id == "19" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      for (i,item) in (("200","In range"),("80","Visible"),("79","Groups"),("69","Detected"),("70","Reports")).enumerate() {
        let x=25+i*55
        content((x,81),text(size:39pt,weight:"bold")[#item.at(0)])
        content((x,58),text(size:14pt)[#item.at(1)])
        if i < 4 {line((x+18,81),(x+35,81),mark:(end:"stealth",fill:black),stroke:0.9pt+black)}
      }
      content((218,106),text(size:18pt,fill:orange)[+ 1 extra vessel report])
      content((138,25),text(size:22pt)[64 aid reports + 6 vessel reports])
      content((138,8),text(size:11pt)[10 missed groups · no extra chart-aid reports])
    })
  } else if id == "20" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      for (i,p) in (("Charleston",harbor),("Norfolk",json("data/map-norfolk.json")),("Portland",json("data/map-portland.json"))).enumerate() {
        basemap(p.at(1),origin:(i*93,16),w:90,h:108,grid-step:5000,point-radius:0.55)
        content((i*93+45,7),text(size:16pt)[#p.at(0)])
      }
    })
  } else if id == "21" { tech-search-model() }
  else if id == "22" { tech-budget() }
  else if id == "23" { tech-refinement() }
  else if id == "24" { tech-pruning() }
  else if id == "25" { include "pruning/witness.typ" }
  else if id == "28" { tech-workload() }
  else if id == "27" {
    trace-figure(json("pruning/movie/body-poster.json"),body:true)
  } else if id == "29" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      let local=json("data/map-result-harbor.json")
      let rays=local.matched_rays.map(r=>r.visible_ray)
      basemap(local,origin:(0,10),w:157,h:115,grid-step:1000,rays:rays,point-radius:0.6)
      let tr=mapper(local,(0,10),157,115)
      symbol((tr.point)(result.estimate),kind:"estimate",r:1.7)
      let xy=p=>(219+(p.at(0) - result.estimate.at(0))*10,78+(p.at(1) - result.estimate.at(1))*10)
      for t in range(-3,4) {
        line((189,78+t*10),(249,78+t*10),stroke:0.35pt+rgb("#aaaaaa"))
        line((219+t*10,48),(219+t*10,108),stroke:0.35pt+rgb("#aaaaaa"))
      }
      line(xy(result.estimate),xy(result.truth),stroke:1.2pt+purple)
      symbol(xy(result.truth),kind:"truth",r:2)
      symbol(xy(result.estimate),kind:"estimate",r:2)
      label((219,119),[1 m grid],size:13pt)
      big((219,31),[2.06 m],[64 / 64 aid bearings matched],col:purple,size:34pt)
      content((219,6),text(size:11pt)[× Truth   ◆ Selected estimate])
      content((78,3),text(size:10pt)[Matched rays clipped to harbor crop])
    })
  } else if id == "32" or id == "33" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      basemap(harbor,origin:(0,10),w:176,h:115,grid-step:5000)
      if id=="32" {
        big((225,89),[8.46 s],[Search wall time],size:42pt)
        content((225,38),text(size:19pt)[Eastern Seaboard])
        content((225,21),text(size:19pt)[3 GPUs])
      } else {
        big((225,102),[2.06 m],[Position error],col:purple,size:34pt)
        big((225,57),[8.46 s],[Full-coast search],size:34pt)
        content((225,17),text(size:17pt)[64 chart-aid bearings])
      }
      content((90,1),text(size:10pt)[Saved simulated Charleston frame · independent truth evaluation])
    })
  } else if id == "30" {
    result-plot()
  } else if id == "26" {
    tech-residual()
  } else if id == "31" {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      polar(frame.measurements,origin:(66,69),r:42,mode:"physical_aid")
      polar(frame.measurements,origin:(211,69),r:42)
      content((66,11),text(size:20pt)[Chart aids])
      content((211,11),text(size:20pt)[Chart aids + AIS])
      label((211,123),[Proposed comparison],size:14pt,fill:garnet)
    })
  } else if id == "A1" { graphic("04") }
  else if id == "A2" { sensor-table() }
  else if id == "A3" { envelope-proof() }
  else if id == "A4" { tech-distinct-proof() }
  else if id == "A5" { exact-arithmetic() }
  else if id == "A6" { epochs() }
  else if id == "A7" { timings() }
  else if id == "A8" { result-plot() }
  else {
    canvas(length:1mm,{
      import draw: *
      rect((0,0),(276,128),stroke:none)
      content((138,64),text(size:24pt)[Technical figure #id])
    })
  }
}

#let id=sys.inputs.at("figure",default:"01")
#if not sys.inputs.at("library",default:"false")=="true" { graphic(id) }
