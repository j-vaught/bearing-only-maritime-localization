// Whole-cell WGS84 bearing envelope. See docs/resident-pruning-bounds.md.
// Conditional on inverse-position <=1cm and configured geodesic/bearing errors.
// The .99 lower scale bound is restricted to this EPSG32618 domain.
__device__ bool true_bearing_envelope(const double* c,const double* o,
    double range_error,double bearing_error,double* angle,double* expansion,double* range_lower){
    *angle=0.;*expansion=180.;*range_lower=0.;
    if(c[0]<-500000.||c[2]>1500000.||c[1]<0.||c[3]>6000000.)return false;
    const double a=6378137.,f=1./298.257223563,Mmin=a*(1-f*(2-f));
    const double inverse_position_error_m=.01;
    double cx=(c[0]+c[2])*.5,cy=(c[1]+c[3])*.5,lon,lat;
    if(!inverse_utm(cx,cy,&lon,&lat))return false;
    double U=o[4],R=hypot(c[2]-c[0],c[3]-c[1])/(2*.99)+2*inverse_position_error_m;
    if(!isfinite(U)||U<0||!isfinite(R))return false;
    double phi=fabs(lat)+R/Mmin;
    if(phi>60./DEG)return false;
    double d;
    if(!geodesic(lon,lat,o[2]/DEG,o[3]/DEG,angle,&d))return false;
    double lo=d-range_error,hi=d+range_error,S=R+U;
    if(!isfinite(d)||!isfinite(S)||hi+S>100000.)return false;
    *range_lower=fmax(0.,lo-S);
    if(lo<=S)return true;
    double delta=1.001*log1p(S/(lo-S))+R*tan(phi)/a;
    if(!isfinite(delta)||delta<0)return false;
    *expansion=fmin(180.,delta*DEG+bearing_error);
    return true;
}
extern "C" __global__ void query_true_bearing_bounds(
    const double* cells,const double* objects,int n,double range_error,double bearing_error,double* out){
    int i=blockIdx.x*blockDim.x+threadIdx.x;if(i>=n)return;
    double angle=0,expansion=180,lower=0;
    bool supported=true_bearing_envelope(cells+4*i,objects+8*i,range_error,bearing_error,&angle,&expansion,&lower);
    if(!supported){angle=0;expansion=180;lower=0;}
    out[4*i]=angle;out[4*i+1]=expansion;out[4*i+2]=lower;out[4*i+3]=supported?1:0;
}
