#import "plot-common.typ": *
#set page(width:320mm,height:145mm,margin:0mm,fill:white)
#set text(font:"Arial",size:9pt)
#set document(author:"J.C. Vaught",title:"Near-coincident charting aid members in detected angular groups")
#let rows=json("aid-clustering.json").sites.filter(r=>r.detected_aid_count>15)
#place(top+left,canvas(length:1mm,{
  import draw: *
  rect((0,0),(320,145),fill:white,stroke:none)
  content((160,139),text(size:14pt)[Near-coincident catalog members in detected aid groups])
  content((160,128),key())
  let ox=18
  let oy=27
  let xx(i)=ox+7+i*14.2
  let yy(n)=oy+n*1.25
  for t in (0,10,20,30,40,50,60) {
    line((ox,yy(t)),(308,yy(t)),stroke:0.3pt+gray)
    content((ox - 2,yy(t)),text(size:8pt)[#t],anchor:"east")
  }
  line((ox,oy),(310,oy),stroke:0.65pt,mark:(end:"stealth",fill:black))
  line((ox,oy),(ox,105),stroke:0.65pt,mark:(end:"stealth",fill:black))
  content((ox,112),text(size:9pt)[Distinct physical-aid member pairs separated by less than 1 cm],anchor:"west")
  for (i,r) in rows.enumerate() {
    let n=r.member_pairs_below_1cm
    if n>0 {
      rect((xx(i)-3.2,oy),(xx(i)+3.2,yy(n)),fill:orange,stroke:none)
      content((xx(i),yy(n)+3),text(size:8pt)[#n])
    }
    symbol((xx(i),oy+0.6),r.status,r:1.0)
    content((xx(i),oy - 4),text(size:8pt)[#r.site_id])
  }
  content((160,10),text(size:9pt)[All eight no-estimate sites contain these near-coincident members; the eleven certified sites and the timeout contain none.])
  content((160,4),text(size:8pt)[Members are retained catalog identities inside angular groups, not separate detected reports · This association does not identify the exact boundary-warning trigger])
}))
