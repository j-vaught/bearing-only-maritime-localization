#import "plot-common.typ": *
#set page(width:320mm,height:180mm,margin:0mm,fill:white)
#set text(font:"Arial",size:9pt)
#set document(author:"J.C. Vaught",title:"All B-site latest saved time accuracy and aid-count relationships")
#let data=json("data.json")
#let plot=sys.inputs.at("plot",default:"time-error")
#let titles=("time-error":[Search time versus position error],"time-aids":[Search time versus detected charting aids],"error-aids":[Position error versus detected charting aids])
#let point(p,r)= {
  import draw: *
  let (x,y)=p
  rect((x - 1.1,y - 1.1),(x+1.1,y+1.1),fill:blue,stroke:0.25pt+white)
}
#let annotated(p,r,d,label:none)={
  let q=(p.at(0)+d.at(0),p.at(1)+d.at(1))
  draw.line(p,q,stroke:0.3pt+rgb("#888888"))
  draw.content(q,text(size:8pt)[#if label==none {r.site}else{label}])
}
#place(top+left,canvas(length:1mm,{
  import draw: *
  rect((0,0),(320,180),fill:white,stroke:none)
  content((160,172),text(size:15pt)[#titles.at(plot)])
  content((160,162),text(size:9pt)[19 completed searches · GPUs 0–2])
  let rows=data.rows.filter(r=>r.status=="completed")
  if plot=="time-aids" {
    let a=axes((20,38),(125,94),(15,105),(5,500),(20,40,60,80,100),(5,10,30,100,300,500),[Detected charting aids],[Search time (s), logarithmic],logy:true)
    let b=axes((178,38),(125,94),(15,105),(7,25),(20,40,60,80,100),(8,12,16,20,24),[Detected charting aids],[Search time (s), detail])
    a.drawing;b.drawing
    let labels=(B16:(6,0),B7:(5,0),B18:(4,3),B2:(3,-4))
    let detail=(B2:(2,6),B4:(1,4),B5:(4,2),B6:(3,3),B7:(0,4),B8:(-5,-3),B9:(4,10),B12:(-3,3),B13:(-2,4),B14:(0,4),B15:(3,4),B17:(-5,3),B18:(-4,3),B19:(3,4),B20:(-5,-1),B21:(0,4),B22:(2,-4),B24:(-4,-4))
    for r in rows {
      let p=((a.x)(r.aids),(a.y)(r.search_s));point(p,r)
      if r.site in labels {annotated(p,r,labels.at(r.site))}
      if r.search_s<=25 {
        let q=((b.x)(r.aids),(b.y)(r.search_s));point(q,r)
        annotated(q,r,detail.at(r.site))
      }
    }
  }else if plot=="time-error" {
    let a=axes((20,38),(125,94),(0.1,30),(5,500),(0.1,0.3,1,3,10,30),(5,10,30,100,300,500),[Position error (m), logarithmic],[Search time (s), logarithmic],logx:true,logy:true)
    let b=axes((178,38),(125,94),(0,4),(7,25),(0,1,2,3,4),(8,12,16,20,24),[Position error (m), detail],[Search time (s), detail])
    a.drawing;b.drawing
    let labels=(B16:(7,0),B18:(0,5),B7:(5,3),B5:(4,3),B21:(-3,-4))
    let detail=(B2:(7,12),B4:(-8,4),B5:(0,4),B6:(-3,12),B7:(3,4),B8:(-4,12),B9:(4,7),B12:(-3,7),B13:(4,10),B14:(8,4),B15:(1,5),B17:(8,4),B19:(-1,10),B20:(-2,10),B21:(-2,2),B22:(-1,10),B24:(-7,6))
    for r in rows.filter(r=>r.error_m!=none) {
      let p=((a.x)(r.error_m),(a.y)(r.search_s));point(p,r)
      if r.site in labels {annotated(p,r,labels.at(r.site))}
      if r.error_m<=4 and r.search_s<=25 {
        let q=((b.x)(r.error_m),(b.y)(r.search_s));point(q,r)
        if r.site not in ("B17","B22") {
          annotated(q,r,detail.at(r.site),label:if r.site=="B14" {[B14 / B17]}else if r.site=="B19" {[B19 / B22]}else{none})
        }
      }
    }
  }else{
    let a=axes((25,38),(270,94),(15,105),(0.1,30),(20,40,60,80,100),(0.1,0.3,1,3,10,30),[Detected charting aids],[Position error (m), logarithmic; lower is better],logy:true)
    a.drawing
    let labels=(B2:(-3,5),B4:(0,4),B5:(0,4),B6:(3,4),B7:(-4,5),B8:(-5,1),B9:(-3,-4),B12:(-1,4),B13:(0,4),B14:(0,4),B15:(3,4),B16:(-2,4),B17:(2,-5),B18:(0,4),B19:(-4,-4),B20:(-3,4),B21:(0,-5),B22:(3,3),B24:(-4,4))
    for r in rows.filter(r=>r.error_m!=none) {
      let p=((a.x)(r.aids),(a.y)(r.error_m));point(p,r)
      annotated(p,r,labels.at(r.site))
    }
  }
  content((160,22),text(size:9pt)[Position error is measured in meters; lower is better.])
  content((160,14),text(size:8pt)[Skipped before search · B1 Searsport 3 aids · B3 Portsmouth 14 aids · B10 South Jersey 8 aids · B23 Port Everglades 15 aids])
  content((160,5),text(size:8pt)[Search-only times exclude preparation, simulation and selection · Completed searches only])
}))
