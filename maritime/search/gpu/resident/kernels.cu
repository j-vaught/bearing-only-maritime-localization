// Experimental resident search kernels. Not execution-verified.
// All query coordinates use EPSG:32618. Every bounded workspace fails closed.
#include <math.h>
#define PI 3.1415926535897932384626433832795
#define DEG (180.0/PI)
#define EPS 1e-6
#define MAX_CROSSINGS 128
struct Map {
    const double *bounds; const int *children; const double *edges; const int *offsets;
};
__device__ bool overlap(const double* b, double x0,double y0,double x1,double y1) {
    return b[0]<=x1 && b[2]>=x0 && b[1]<=y1 && b[3]>=y0;
}
__device__ double cross(double a,double b,double c,double d){return a*d-b*c;}
__device__ int inside(Map m,int layer,double x,double y) {
    int no=m.offsets[3*layer], eo=m.offsets[3*layer+1], count=m.offsets[3*layer+2];
    if (!count) return 0;
    int stack[64], sp=0, parity=0; stack[sp++]=0;
    while(sp) {
        int n=stack[--sp]; const double* b=m.bounds+4*(no+n);
        if(y<b[1]-EPS || y>b[3]+EPS || b[2]<x-EPS)continue;
        int l=m.children[2*(no+n)],r=m.children[2*(no+n)+1];
        if(l>=0){if(sp>61)return -1;stack[sp++]=l;stack[sp++]=r;continue;}
        const double* e=m.edges+4*(eo-l-1);
        double dx=e[2]-e[0],dy=e[3]-e[1],len=dx*dx+dy*dy;
        double t=len>0?fmin(1.,fmax(0.,((x-e[0])*dx+(y-e[1])*dy)/len)):0.;
        if(hypot(x-e[0]-t*dx,y-e[1]-t*dy)<=EPS)return -1;
        if((e[1]>y)!=(e[3]>y)) {
            double ix=e[0]+(y-e[1])*dx/dy;
            if(ix>x)parity^=1;
        }
    }
    return parity;
}
__device__ bool boundary_box(Map m,int layer,double x0,double y0,double x1,double y1) {
    int no=m.offsets[3*layer],count=m.offsets[3*layer+2];
    if(!count)return false;
    int stack[64],sp=0;stack[sp++]=0;
    while(sp){int n=stack[--sp];const double* b=m.bounds+4*(no+n);
        if(!overlap(b,x0-EPS,y0-EPS,x1+EPS,y1+EPS))continue;
        int l=m.children[2*(no+n)],r=m.children[2*(no+n)+1];
        // A segment AABB overlap is deliberately a conservative unresolved result.
        if(l<0)return true;
        if(sp>61)return true;
        stack[sp++]=l;stack[sp++]=r;
    }return false;
}
__device__ int point(Map m,double x,double y){
    int l=inside(m,0,x,y),c=inside(m,1,x,y),u=inside(m,2,x,y);
    if(u!=0||c!=1||l<0)return 2;
    return l==1?1:0;
}
__device__ bool covers_box(Map m,int layer,double x0,double y0,double x1,double y1){
    return inside(m,layer,(x0+x1)*.5,(y0+y1)*.5)==1 &&
           !boundary_box(m,layer,x0,y0,x1,y1);
}
__device__ int ray(Map m,double ax,double ay,double bx,double by){
    double dx=bx-ax,dy=by-ay,len=hypot(dx,dy);
    if(len<EPS)return point(m,ax,ay)==0?0:2;
    double ts[MAX_CROSSINGS];int nt=2;ts[0]=0.;ts[1]=1.;bool unsure=false;
    for(int layer=0;layer<3;++layer){
        int no=m.offsets[3*layer],eo=m.offsets[3*layer+1],nc=m.offsets[3*layer+2];
        if(!nc)continue;
        int stack[64],sp=0;stack[sp++]=0;
        while(sp){int n=stack[--sp];const double* bb=m.bounds+4*(no+n);
            if(!overlap(bb,fmin(ax,bx)-EPS,fmin(ay,by)-EPS,fmax(ax,bx)+EPS,fmax(ay,by)+EPS))continue;
            int l=m.children[2*(no+n)],r=m.children[2*(no+n)+1];
            if(l>=0){if(sp>61)return 2;stack[sp++]=l;stack[sp++]=r;continue;}
            const double* e=m.edges+4*(eo-l-1);double ex=e[2]-e[0],ey=e[3]-e[1];
            double den=cross(dx,dy,ex,ey),qx=e[0]-ax,qy=e[1]-ay;
            if(fabs(den)<=1e-12*fmax(1.,len*hypot(ex,ey))){unsure=true;continue;}
            double t=cross(qx,qy,ex,ey)/den,s=cross(qx,qy,dx,dy)/den;
            if(t>=0&&t<=1&&s>=0&&s<=1){
                if(nt==MAX_CROSSINGS)return 2;
                ts[nt++]=t;
                if(s<1e-12||s>1-1e-12)unsure=true;
            }
        }
    }
    for(int i=1;i<nt;++i){double t=ts[i];int j=i-1;while(j>=0&&ts[j]>t){ts[j+1]=ts[j];--j;}ts[j+1]=t;}
    double land=0.,unknown=0.;
    for(int i=1;i<nt;++i){double width=(ts[i]-ts[i-1])*len;if(width<=0)continue;
        double t=(ts[i]+ts[i-1])*.5;int p=point(m,ax+t*dx,ay+t*dy);
        if(p==1)land+=width;else if(p==2)unknown+=width;
    }
    if(land>EPS)return 1;
    if(unknown>EPS||unsure||point(m,ax,ay)==2||point(m,bx,by)==2)return 2;
    return 0;
}
// Sixth-order inverse Krueger transverse Mercator on WGS84, zone 18 north.
// Runtime numerical parity must be checked against PROJ before acceptance.
__device__ bool inverse_utm(double x,double y,double* lon,double* lat){
    const double a=6378137.,f=1./298.257223563,n=f/(2-f),e=sqrt(f*(2-f));
    double n2=n*n,n3=n2*n,n4=n2*n2,n5=n4*n,n6=n3*n3;
    double A=a/(1+n)*(1+n2/4+n4/64+n6/256);
    double beta[6]={n/2-2*n2/3+37*n3/96-n4/360-81*n5/512+96199*n6/604800,
        n2/48+n3/15-437*n4/1440+46*n5/105-1118711*n6/3870720,
        17*n3/480-37*n4/840-209*n5/4480+5569*n6/90720,
        4397*n4/161280-11*n5/504-830251*n6/7257600,
        4583*n5/161280-108847*n6/3991680,20648693*n6/638668800};
    double eta=(x-500000.)/(.9996*A),xi=y/(.9996*A),ep=eta,xp=xi;
    for(int j=1;j<=6;++j){xp-=beta[j-1]*sin(2*j*xi)*cosh(2*j*eta);ep-=beta[j-1]*cos(2*j*xi)*sinh(2*j*eta);}
    double tp=sin(xp)/hypot(sinh(ep),cos(xp)),tau=tp/(1-e*e);
    bool ok=false;
    for(int i=0;i<12;++i){double tau1=hypot(1.,tau),sig=sinh(e*atanh(e*tau/tau1));
        double calc=hypot(1.,sig)*tau-sig*tau1;
        double delta=(tp-calc)*(1+(1-e*e)*tau*tau)/((1-e*e)*tau1*hypot(1.,calc));
        tau+=delta;if(fabs(delta)<1e-13*fmax(1.,fabs(tau))){ok=true;break;}}
    *lat=atan(tau);*lon=-75./DEG+atan2(sinh(ep),cos(xp));
    return ok&&isfinite(*lon)&&isfinite(*lat)&&fabs(*lat)<PI/2;
}
__device__ bool geodesic(double lon1,double lat1,double lon2,double lat2,double* bearing,double* dist){
    const double f=1./298.257223563,a=6378137.,b=a*(1-f);
    double U1=atan((1-f)*tan(lat1)),U2=atan((1-f)*tan(lat2));
    double s1=sin(U1),c1=cos(U1),s2=sin(U2),c2=cos(U2),L=lon2-lon1,lam=L;
    double ss=0,cs=0,sig=0,ca2=0,c2m=0;bool ok=false;
    for(int k=0;k<100;++k){double sl=sin(lam),cl=cos(lam);
        ss=hypot(c2*sl,c1*s2-s1*c2*cl);if(ss<1e-16){*bearing=0;*dist=0;return true;}
        cs=s1*s2+c1*c2*cl;sig=atan2(ss,cs);double sa=c1*c2*sl/ss;
        ca2=fmax(0.,1-sa*sa);c2m=ca2>1e-16?cs-2*s1*s2/ca2:0.;
        double C=f/16*ca2*(4+f*(4-3*ca2));double prev=lam;
        lam=L+(1-C)*f*sa*(sig+C*ss*(c2m+C*cs*(-1+2*c2m*c2m)));
        if(fabs(lam-prev)<1e-13){ok=true;break;}}
    if(!ok)return false;
    double u=ca2*(a*a-b*b)/(b*b),A=1+u/16384*(4096+u*(-768+u*(320-175*u))),
        B=u/1024*(256+u*(-128+u*(74-47*u)));
    double delta=B*ss*(c2m+B/4*(cs*(-1+2*c2m*c2m)-B/6*c2m*(-3+4*ss*ss)*(-3+4*c2m*c2m)));
    *dist=b*A*(sig-delta);*bearing=fmod(atan2(c2*sin(lam),c1*s2-s1*c2*cos(lam))*DEG+360.,360.);
    return isfinite(*dist)&&isfinite(*bearing);
}
__device__ double angular(double a,double b){return fabs(remainder(a-b,360.));}
__device__ bool barrier(const double* rects,int nr,const double* cell,double x,double y){
    for(int j=0;j<nr;++j){const double* r=rects+4*j;
        for(int axis=0;axis<2;++axis){double q=(r[axis]+r[axis+2])*.5;
            double cmin=cell[axis],cmax=cell[axis+2],target=axis?y:x;
            if(!((q>cmax+EPS&&q<target-EPS)||(q<cmin-EPS&&q>target+EPS)))continue;
            double lo=1e300,hi=-1e300;
            for(int i=0;i<4;++i){double a=cell[(i&1)?2:0],b=cell[(i&2)?3:1];
                double p=axis?b:a,other=axis?a:b,t=(q-p)/(target-p);
                double hit=other+t*((axis?x:y)-other);lo=fmin(lo,hit);hi=fmax(hi,hit);}
            int other=1-axis;if(lo>r[other]+EPS&&hi<r[other+2]-EPS)return true;
        }
    }return false;
}
extern "C" __global__ void seed_cells(long long begin,long long end,int ny,
    double xmin,double ymin,double xmax,double ymax,double size,double* cells){
    long long i=(long long)blockIdx.x*blockDim.x+threadIdx.x;if(begin+i>=end)return;
    long long k=begin+i;double x=xmin+(k/ny)*size,y=ymin+(k%ny)*size;
    cells[4*i]=x;cells[4*i+1]=y;cells[4*i+2]=fmin(x+size,xmax);cells[4*i+3]=fmin(y+size,ymax);
}
// state codes 0=retain, 1=whole-cell land, 2=no positive match, 3=too few markers.
extern "C" __global__ void prune_cells(const double* cells,int count,
    const double* mb,const int* mc,const double* me,const int* mo,
    const double* ob,const int* oc,const double* objects,int on,
    const double* bearings,const double* tolerances,int nd,
    const double* rects,int nr,double radius,double scale,double azimuth,
    double max_uncertainty,int marker_proofs,int distinct_proofs,int* state){
    int i=blockIdx.x*blockDim.x+threadIdx.x;if(i>=count)return;
    const double* c=cells+4*i;Map m={mb,mc,me,mo};state[i]=0;
    bool known=covers_box(m,1,c[0],c[1],c[2],c[3]) &&
        inside(m,2,(c[0]+c[2])*.5,(c[1]+c[3])*.5)==0 &&
        !boundary_box(m,2,c[0],c[1],c[2],c[3]);
    if(known&&covers_box(m,0,c[0],c[1],c[2],c[3])){state[i]=1;return;}
    if(!known||!marker_proofs||nd==0||nd>256)return;
    double margin=radius*scale+EPS;
    if(!covers_box(m,3,c[0]-margin,c[1]-margin,c[2]+margin,c[3]+margin))return;
    if(!covers_box(m,1,c[0]-margin,c[1]-margin,c[2]+margin,c[3]+margin)||
       boundary_box(m,2,c[0]-margin,c[1]-margin,c[2]+margin,c[3]+margin)||
       inside(m,2,(c[0]+c[2])*.5,(c[1]+c[3])*.5)!=0)return;
    bool possible[256];for(int d=0;d<nd;++d)possible[d]=false;
    int candidates=0,stack[64],sp=0;if(on)stack[sp++]=0;
    double r=margin+max_uncertainty*scale,cx=(c[0]+c[2])*.5,cy=(c[1]+c[3])*.5;
    double cr=hypot(c[2]-c[0],c[3]-c[1])*.5;
    while(sp){int node=stack[--sp];if(!overlap(ob+4*node,c[0]-r,c[1]-r,c[2]+r,c[3]+r))continue;
        int l=oc[2*node],rr=oc[2*node+1];if(l>=0){if(sp>61)return;stack[sp++]=l;stack[sp++]=rr;continue;}
        const double* o=objects+8*(-l-1);if(o[6]==2)continue;
        double unc=o[4]*scale,dx=fmax(fmax(c[0]-o[0],0.),o[0]-c[2]),dy=fmax(fmax(c[1]-o[1],0.),o[1]-c[3]);
        if(hypot(dx,dy)-unc>margin)continue;
        if(unc==0&&barrier(rects,nr,c,o[0],o[1]))continue;
        ++candidates;double dist=hypot(o[0]-cx,o[1]-cy),rad=cr+unc;
        double angle=fmod(atan2(o[0]-cx,o[1]-cy)*DEG+360.,360.);
        double expand=dist<=rad?180.:asin(fmin(1.,rad/dist))*DEG;
        for(int d=0;d<nd;++d)if(angular(angle,bearings[d])<=tolerances[d]+expand+azimuth+o[5]+1e-8)possible[d]=true;
    }
    for(int d=0;d<nd;++d)if(!possible[d]){state[i]=2;return;}
    if(distinct_proofs&&candidates<nd)state[i]=3;
}
extern "C" __global__ void query_points(const double* xy,int n,const double* mb,
    const int* mc,const double* me,const int* mo,int* codes){
    int i=blockIdx.x*blockDim.x+threadIdx.x;if(i<n){Map m={mb,mc,me,mo};codes[i]=point(m,xy[2*i],xy[2*i+1]);}
}
extern "C" __global__ void query_rays(const double* rays,int n,const double* mb,
    const int* mc,const double* me,const int* mo,int* codes){
    int i=blockIdx.x*blockDim.x+threadIdx.x;if(i<n){Map m={mb,mc,me,mo};const double* r=rays+4*i;codes[i]=ray(m,r[0],r[1],r[2],r[3]);}
}
// Gather, ellipsoidal prediction, visibility and circular connected grouping.
// flags 1=unknown geometry/support, 2=capacity, 4=numerical; never truncate silently.
extern "C" __global__ void predict_groups(const double* cells,int count,
    const double* mb,const int* mc,const double* me,const int* mo,
    const double* ob,const int* oc,const double* objects,int on,
    double radius,double scale,double resolution,int capacity,
    double* angles,double* ranges,int* ids,int* exact,
    double* group_angles,double* group_extents,int* group_ids,int* group_exact,
    int* group_counts,int* flags,int* point_states){
    int i=blockIdx.x*blockDim.x+threadIdx.x;if(i>=count)return;
    Map m={mb,mc,me,mo};const double* c=cells+4*i;double x=(c[0]+c[2])*.5,y=(c[1]+c[3])*.5;
    point_states[i]=point(m,x,y);flags[i]=0;group_counts[i]=0;
    if(point_states[i]==1)return;
    if(point_states[i]==2)flags[i]|=1;
    double lon,lat;if(!inverse_utm(x,y,&lon,&lat)){flags[i]|=4;return;}
    int stack[64],sp=0,n=0;if(on)stack[sp++]=0;double r=radius*scale+EPS;
    int base=i*capacity;
    while(sp){int node=stack[--sp];if(!overlap(ob+4*node,x-r,y-r,x+r,y+r))continue;
        int l=oc[2*node],rr=oc[2*node+1];if(l>=0){if(sp>61){flags[i]|=4;return;}stack[sp++]=l;stack[sp++]=rr;continue;}
        int id=-l-1;const double* o=objects+8*id;double a,d;
        if(!geodesic(lon,lat,o[2]/DEG,o[3]/DEG,&a,&d)){flags[i]|=4;continue;}
        if(d>radius)continue;
        int vis=ray(m,x,y,o[0],o[1]);if(vis==1)continue;
        if(vis==2)flags[i]|=1;
        if(n==capacity){flags[i]|=2;return;}
        int k=n++;
        while(k>0&&(angles[base+k-1]>a||(angles[base+k-1]==a&&ids[base+k-1]>id))){
            angles[base+k]=angles[base+k-1];ranges[base+k]=ranges[base+k-1];
            ids[base+k]=ids[base+k-1];exact[base+k]=exact[base+k-1];--k;}
        angles[base+k]=a;ranges[base+k]=d;ids[base+k]=id;
        exact[base+k]=(vis==0&&o[4]==0&&o[7]==1);
    }
    if(n==0)return;
    int start=0;bool gap=false;
    for(int j=0;j<n;++j){double a=angles[base+(j+1)%n]-angles[base+j];if(a<0)a+=360.;
        if(a>resolution){start=(j+1)%n;gap=true;break;}}
    int rep=-1,g=0,gstart=0;bool certain=true;
    for(int j=0;j<n;++j){int k=(start+j)%n,id=ids[base+k];
        if(rep<0||ranges[base+k]<ranges[base+rep]||(ranges[base+k]==ranges[base+rep]&&id<ids[base+rep]))rep=k;
        certain=certain&&(exact[base+k]!=0);
        double delta=angles[base+(k+1)%n]-angles[base+k];if(delta<0)delta+=360.;
        if((gap&&delta>resolution)||j==n-1){
            int rid=ids[base+rep];const double* o=objects+8*rid;
            double extent=0.;for(int z=gstart;z<=j;++z){int k2=(start+z)%n;extent=fmax(extent,angular(angles[base+k2],angles[base+rep])+objects[8*ids[base+k2]+5]);}
            if(o[6]!=2){group_angles[base+g]=angles[base+rep];group_extents[base+g]=fmin(180.,extent);
                group_ids[base+g]=rid;group_exact[base+g]=(certain&&o[6]==1);++g;}
            rep=-1;certain=true;gstart=j+1;
        }
    }group_counts[i]=g;
}
extern "C" __global__ void candidate_scope(const double* cells,int n,
    const double* mb,const int* mc,const double* me,const int* mo,double radius,int* scope){
    int i=blockIdx.x*blockDim.x+threadIdx.x;if(i>=n)return;
    Map m={mb,mc,me,mo};const double* c=cells+4*i;
    double x=(c[0]+c[2])*.5,y=(c[1]+c[3])*.5,r=radius+EPS;
    scope[i]=covers_box(m,3,x-r,y-r,x+r,y+r)&&covers_box(m,1,x-r,y-r,x+r,y+r)&&
        !boundary_box(m,2,x-r,y-r,x+r,y+r)&&inside(m,2,x,y)==0;
}
extern "C" __global__ void query_geodesics(const double* xy,const double* ll,int n,double* out,int* flags){
    int i=blockIdx.x*blockDim.x+threadIdx.x;if(i>=n)return;
    double lon,lat,bearing,distance;
    bool ok=inverse_utm(xy[2*i],xy[2*i+1],&lon,&lat);
    ok=ok&&geodesic(lon,lat,ll[2*i]/DEG,ll[2*i+1]/DEG,&bearing,&distance);
    flags[i]=ok?0:1;out[4*i]=lon*DEG;out[4*i+1]=lat*DEG;
    out[4*i+2]=bearing;out[4*i+3]=distance;
}
