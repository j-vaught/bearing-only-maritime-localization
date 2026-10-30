#import "plot-common.typ": *
#set page(width:320mm,height:180mm,margin:0mm,fill:white)
#set text(font:"Arial",size:10pt)
#set document(author:"J.C. Vaught",title:"Corrected eight-site runtime and accuracy relationships")
#let data=json("data.json")
#let plot=sys.inputs.at("plot",default:"time-error")
#let specs=(
  "time-error":(title:[Search time versus position error],xfield:"error_m",yfield:"search_s",xb:(0,25),yb:(0,25),xticks:(0,5,10,15,20,25),yticks:(0,5,10,15,20,25),xlabel:[Position error (m), lower is better],ylabel:[Search time (s)]),
  "time-aids":(title:[Search time versus detected charting aids],xfield:"aids",yfield:"search_s",xb:(15,65),yb:(0,25),xticks:(15,25,35,45,55,65),yticks:(0,5,10,15,20,25),xlabel:[Detected charting aids],ylabel:[Search time (s)]),
  "error-aids":(title:[Position error versus detected charting aids],xfield:"aids",yfield:"error_m",xb:(15,65),yb:(0,25),xticks:(15,25,35,45,55,65),yticks:(0,5,10,15,20,25),xlabel:[Detected charting aids],ylabel:[Position error (m), lower is better]),
)
#let cfg=specs.at(plot)
#place(top+left,canvas(length:1mm,{
  import draw: *
  rect((0,0),(320,180),fill:white,stroke:none)
  content((160,172),text(size:16pt)[#cfg.title])
  content((160,161),text(size:10pt)[Eight sites · 20 cm chart-aid merge rule · GPUs 0–2 · Warnings unchanged])
  let a=axes((26,39),(270,104),cfg.xb,cfg.yb,cfg.xticks,cfg.yticks,cfg.xlabel,cfg.ylabel)
  a.drawing
  let offsets=if plot=="time-error" {
    (B2:(9,-4),B6:(6,5),B7:(6,3),B18:(0,6),B19:(0,10),B20:(10,8),B21:(0,-7),B22:(0,10))
  }else if plot=="time-aids" {
    (B2:(0,5),B6:(0,5),B7:(0,5),B18:(0,5),B19:(0,5),B20:(0,-5),B21:(0,5),B22:(0,5))
  }else{
    (B2:(0,5),B6:(0,5),B7:(0,5),B18:(0,5),B19:(0,-5),B20:(-4,6),B21:(0,5),B22:(0,-5))
  }
  for r in data.rows {
    let p=((a.x)(r.at(cfg.xfield)),(a.y)(r.at(cfg.yfield)))
    let col=if r.site=="B18" {orange}else{blue}
    rect((p.at(0)-1.2,p.at(1)-1.2),(p.at(0)+1.2,p.at(1)+1.2),fill:col,stroke:0.3pt+white)
    if not (plot=="time-error" and r.site=="B22") {
      let d=offsets.at(r.site)
      let label=(p.at(0)+d.at(0),p.at(1)+d.at(1))
      line(p,label,stroke:0.35pt+rgb("#888888"))
      content(label,text(size:10pt)[#if plot=="time-error" and r.site=="B19" {[B19 / B22]}else{r.site}])
    }
  }
  content((160,21),text(size:9pt)[B2 Portland · B6 New Haven · B7 Bridgeport · B18 Savannah])
  content((160,14),text(size:9pt)[B19 Brunswick · B20 Jacksonville · B21 Port Canaveral · B22 Palm Beach])
  content((160,5),text(size:8pt)[Search-only time excludes shared preparation, simulation and selection · Error is WGS84 distance to truth after estimate freezing])
}))
