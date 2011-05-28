'''
Created on May 16, 2011

@author: Jacob Salomonsen
'''

import numpy as np
import pycuda.driver as cuda
import pycuda.autoinit
from pycuda.compiler import SourceModule

' Simulation attributes '
nx      = 10
ny      = 10
it      = 10

' Constants '
omega   = 1.0
density = 1.0
t1      = 4/9.0
t2      = 1/9.0
t3      = 1/36.0
deltaU  = 1e-7
c_squ   = 1/3.0

' CUDA specific '
#threadsPerBlock = 256
#blocksPerGrid   = (nx*ny + threadsPerBlock - 1) / threadsPerBlock
dim         = 16
blockDimX   = min(nx,dim)
blockDimY   = min(ny,dim)
gridDimX    = (nx+dim-1)/dim
gridDimY    = (ny+dim-1)/dim

' Create the main arrays '
F           = np.zeros((9,nx,ny), dtype=float).astype(np.float32)
F[:,:,:]   += density/9.0
T           = np.copy(F)
FEQ         = np.copy(F)
BOUNCEBACK  = np.zeros(F.shape, dtype=float).astype(np.float32)
DENSITY     = np.zeros((nx,ny), dtype=float).astype(np.float32)
UX          = np.copy(DENSITY)
UY          = np.copy(DENSITY)

' Create the scenery '
BOUND   = np.zeros((nx,ny), dtype=float).astype(np.float32)

#for i in xrange(nx):
#    for j in xrange(ny):
#        if ((i-4)**2+(j-5)**2+(5-6)**2) < 6:
#            BOUND [i,j] = 1.0
#BOUND[:,0] = 1.0

#BOUND = np.random.randint(2, size=(nx,ny)).astype(np.float32)

BOUND[0,:] = 1.0

mod = SourceModule("""
    //   F4  F3  F2
    //     \ | /
    //  F5--F0--F1
    //    / | \
    // F6  F7  F8
    
    __global__ void propagateKernel(float *F, float *T) {
        int nx = blockDim.x * gridDim.x;
        int ny = blockDim.y * gridDim.y;
        int size = nx * ny;
        
        int x     = threadIdx.x + blockIdx.x * blockDim.x;
        int y     = threadIdx.y + blockIdx.y * blockDim.y;
        
        // nearest neighbours
        int F1 = (x==0?nx-1:x-1) + y * nx; // +x
        int F3 = x + (y==0?ny-1:y-1) * nx; // +y
        int F5 = (x==nx-1?0:x+1) + y * nx; // -x
        int F7 = x + (y==ny-1?0:y+1) * nx; // -y
        
        // next-nearest neighbours
        int F2 = (x==0?nx-1:x-1) +
                 (y==0?ny-1:y-1) * nx; //+x+y
                 
        int F4 = (x==nx-1?0:x+1) +
                 (y==0?ny-1:y-1) * nx; //-x+y
        
        int F6 = (x==nx-1?0:x+1) + 
                 (y==ny-1?0:y+1) * nx; //-x-y
                 
        int F8 = (x==0?nx-1:x-1) +
                 (y==ny-1?0:y+1) * nx; //+x-y
        
        // current point
        int cur = x + y * nx;
        
        // propagate nearest
        F[1*size + cur] = T[1*size + F1];
        F[3*size + cur] = T[3*size + F3];
        F[5*size + cur] = T[5*size + F5];
        F[7*size + cur] = T[7*size + F7];
        
        // propagate next-nearest
        F[2*size + cur] = T[2*size + F2];
        F[4*size + cur] = T[4*size + F4];
        F[6*size + cur] = T[6*size + F6];
        F[8*size + cur] = T[8*size + F8];
    }
    
    __global__ void densityKernel(float *F, float *BOUND, float * BOUNCEBACK, float *D, float *UX, float *UY) {
        int size  = blockDim.x * gridDim.x * blockDim.y * gridDim.y;
        int x     = threadIdx.x + blockIdx.x * blockDim.x;
        int y     = threadIdx.y + blockIdx.y * blockDim.y;
        int cur   = x + y * blockDim.x * gridDim.x;
        if(BOUND[cur] == 1.0f) {
            BOUNCEBACK[1*size + cur] = F[5*size + cur];
            BOUNCEBACK[2*size + cur] = F[6*size + cur];
            BOUNCEBACK[3*size + cur] = F[7*size + cur];
            BOUNCEBACK[4*size + cur] = F[8*size + cur];
            BOUNCEBACK[5*size + cur] = F[1*size + cur];
            BOUNCEBACK[6*size + cur] = F[2*size + cur];
            BOUNCEBACK[7*size + cur] = F[3*size + cur];
            BOUNCEBACK[8*size + cur] = F[4*size + cur];
        }     
           
        D[cur] = F[0*size + cur] + 
                 F[1*size + cur] +
                 F[2*size + cur] +
                 F[3*size + cur] +
                 F[4*size + cur] +
                 F[5*size + cur] +
                 F[6*size + cur] +
                 F[7*size + cur] +
                 F[8*size + cur];
        
        UX[cur] = ((F[1*size + cur] + F[2*size + cur] + F[8*size + cur]) -
                   (F[4*size + cur] + F[5*size + cur] + F[6*size + cur])) 
                    / D[cur];
                 
        UY[cur] = ((F[2*size + cur] + F[3*size + cur] + F[4*size + cur]) -
                   (F[6*size + cur] + F[7*size + cur] + F[8*size + cur])) 
                    / D[cur];
                    
        if(x == 0)
            UX[cur] += 0.00000001f;
            
        if(BOUND[cur] == 1.0f) {
            D[cur] = 0.0f;
            UX[cur] = 0.0f;
            UY[cur] = 0.0f;
        }
    }
    
     __global__ void eqKernel(float *F, float* FEQ, float *DENSITY, float *UX, 
    float *UY, float *U_SQU, float *U_C2, float *U_C4, float *U_C6, float *U_C8) {
        int size = blockDim.x * gridDim.x * blockDim.y * gridDim.y;
        int x     = threadIdx.x + blockIdx.x * blockDim.x;
        int y     = threadIdx.y + blockIdx.y * blockDim.y;
        int cur   = x + y * blockDim.x * gridDim.x;
        
        // constants
        float t1 = 0.44444444444444442f;
        float t2 = 0.1111111111111111f;
        float t3 = 0.027777777777777776f;
        float c_squ = 0.33333333333333331f;
        float omega = 1.0f;
        
        U_SQU[cur] = UX[cur]*UX[cur] + UY[cur]*UY[cur];
        U_C2[cur]  =UX[cur]+UY[cur];
        U_C4[cur]  =-UX[cur]+UY[cur];
        U_C6[cur]  =-U_C2[cur];
        U_C8[cur]  =-U_C4[cur];
        
        // Calculate equilibrium distribution: stationary
        FEQ[0*size + cur] = t1*DENSITY[cur]*(1-U_SQU[cur]/(2*c_squ));
        
        // nearest-neighbours
        FEQ[1*size + cur]=t2*DENSITY[cur]*(1+UX[cur]/c_squ+0.5f*(UX[cur]/c_squ)*(UX[cur]/c_squ)-U_SQU[cur]/(2*c_squ));
        FEQ[3*size + cur]=t2*DENSITY[cur]*(1+UY[cur]/c_squ+0.5f*(UY[cur]/c_squ)*(UY[cur]/c_squ)-U_SQU[cur]/(2*c_squ));
        FEQ[5*size + cur]=t2*DENSITY[cur]*(1-UX[cur]/c_squ+0.5f*(UX[cur]/c_squ)*(UX[cur]/c_squ)-U_SQU[cur]/(2*c_squ));
        FEQ[7*size + cur]=t2*DENSITY[cur]*(1-UY[cur]/c_squ+0.5f*(UY[cur]/c_squ)*(UY[cur]/c_squ)-U_SQU[cur]/(2*c_squ));
        
        // next-nearest neighbours
        FEQ[2*size + cur]=t3*DENSITY[cur]*(1+U_C2[cur]/c_squ+0.5f*(U_C2[cur]/c_squ)*(U_C2[cur]/c_squ)-U_SQU[cur]/(2*c_squ));
        FEQ[4*size + cur]=t3*DENSITY[cur]*(1+U_C4[cur]/c_squ+0.5f*(U_C4[cur]/c_squ)*(U_C4[cur]/c_squ)-U_SQU[cur]/(2*c_squ));
        FEQ[6*size + cur]=t3*DENSITY[cur]*(1+U_C6[cur]/c_squ+0.5f*(U_C6[cur]/c_squ)*(U_C6[cur]/c_squ)-U_SQU[cur]/(2*c_squ));
        FEQ[8*size + cur]=t3*DENSITY[cur]*(1+U_C8[cur]/c_squ+0.5f*(U_C8[cur]/c_squ)*(U_C8[cur]/c_squ)-U_SQU[cur]/(2*c_squ));
        
        F[0*size + cur]=omega*FEQ[0*size + cur]+(1-omega)*F[0*size + cur];
        F[1*size + cur]=omega*FEQ[1*size + cur]+(1-omega)*F[1*size + cur];
        F[2*size + cur]=omega*FEQ[2*size + cur]+(1-omega)*F[2*size + cur];
        F[3*size + cur]=omega*FEQ[3*size + cur]+(1-omega)*F[3*size + cur];
        F[4*size + cur]=omega*FEQ[4*size + cur]+(1-omega)*F[4*size + cur];
        F[5*size + cur]=omega*FEQ[5*size + cur]+(1-omega)*F[5*size + cur];
        F[6*size + cur]=omega*FEQ[6*size + cur]+(1-omega)*F[6*size + cur];
        F[7*size + cur]=omega*FEQ[7*size + cur]+(1-omega)*F[7*size + cur];
        F[8*size + cur]=omega*FEQ[8*size + cur]+(1-omega)*F[8*size + cur];
    }
    
    __global__ void bouncebackKernel(float *F, float *BOUNCEBACK, float *BOUND) {
        int size = blockDim.x * gridDim.x * blockDim.y * gridDim.y;
        int x     = threadIdx.x + blockIdx.x * blockDim.x;
        int y     = threadIdx.y + blockIdx.y * blockDim.y;
        int cur   = x + y * blockDim.x * gridDim.x;
        if(BOUND[cur] == 1.0f) {
            F[1*size + cur] = BOUNCEBACK[1*size + cur];
            F[2*size + cur] = BOUNCEBACK[2*size + cur];
            F[3*size + cur] = BOUNCEBACK[3*size + cur];
            F[4*size + cur] = BOUNCEBACK[4*size + cur];
            F[5*size + cur] = BOUNCEBACK[5*size + cur];
            F[6*size + cur] = BOUNCEBACK[6*size + cur];
            F[7*size + cur] = BOUNCEBACK[7*size + cur];
            F[8*size + cur] = BOUNCEBACK[8*size + cur];
        }
    }
    """)

' Allocate memory on the GPU '
F_gpu       = cuda.mem_alloc(F.size * F.dtype.itemsize)
T_gpu       = cuda.mem_alloc(T.size * F.dtype.itemsize)
FEQ_gpu     = cuda.mem_alloc(FEQ.size * FEQ.dtype.itemsize)
BOUND_gpu   = cuda.mem_alloc(BOUND.size * BOUND.dtype.itemsize)
BOUNCEBACK_gpu = cuda.mem_alloc(BOUNCEBACK.size * BOUNCEBACK.dtype.itemsize)
DENSITY_gpu = cuda.mem_alloc(DENSITY.size * DENSITY.dtype.itemsize)
UX_gpu      = cuda.mem_alloc(UX.size * UX.dtype.itemsize)
UY_gpu      = cuda.mem_alloc(UY.size * UY.dtype.itemsize)

#===============================================================================
# ' following allocated for eqKernel '
# U_SQU_gpu   = cuda.mem_alloc(DENSITY.size * DENSITY.dtype.itemsize)
# U_C2_gpu    = cuda.mem_alloc(DENSITY.size * DENSITY.dtype.itemsize)
# U_C4_gpu    = cuda.mem_alloc(DENSITY.size * DENSITY.dtype.itemsize)
# U_C6_gpu    = cuda.mem_alloc(DENSITY.size * DENSITY.dtype.itemsize)
# U_C8_gpu    = cuda.mem_alloc(DENSITY.size * DENSITY.dtype.itemsize)
#===============================================================================

' Get kernel handles '
prop = mod.get_function("propagateKernel")
density = mod.get_function("densityKernel")
eq = mod.get_function("eqKernel")
bounceback = mod.get_function("bouncebackKernel")

' Copy constants and variables only changed on gpu ' 
cuda.memcpy_htod(BOUND_gpu, BOUND)
cuda.memcpy_htod(BOUNCEBACK_gpu, BOUNCEBACK)

cuda.memcpy_htod(F_gpu, F)
cuda.memcpy_htod(FEQ_gpu, FEQ)

cuda.memcpy_htod(DENSITY_gpu, DENSITY)
cuda.memcpy_htod(UX_gpu, UX)
cuda.memcpy_htod(UY_gpu, UY)

#===============================================================================
# cuda.memcpy_htod(U_SQU_gpu, DENSITY)
# cuda.memcpy_htod(U_C2_gpu, DENSITY)
# cuda.memcpy_htod(U_C4_gpu, DENSITY)
# cuda.memcpy_htod(U_C6_gpu, DENSITY)
# cuda.memcpy_htod(U_C8_gpu, DENSITY)
#===============================================================================

ts=0
while(ts<it):
    cuda.memcpy_dtoh(F,F_gpu)
    T[:] = F
    cuda.memcpy_htod(T_gpu, T)
    
    prop(F_gpu, T_gpu, block=(blockDimX,blockDimY,1), grid=(gridDimX,gridDimY))
    density(F_gpu, BOUND_gpu, BOUNCEBACK_gpu, DENSITY_gpu, UX_gpu, UY_gpu,
            block=(blockDimX,blockDimY,1), grid=(gridDimX,gridDimY))
    
    cuda.memcpy_dtoh(F, F_gpu)
    cuda.memcpy_dtoh(DENSITY, DENSITY_gpu)
    cuda.memcpy_dtoh(UX, UX_gpu)
    cuda.memcpy_dtoh(UY, UY_gpu)
    
    # TODO: Make following parallel...
    U_SQU = UX**2 + UY**2
    U_C2=UX+UY
    U_C4=-UX+UY
    U_C6=-U_C2
    U_C8=-U_C4
    
    # Calculate equilibrium distribution: stationary
    FEQ[8,:,:]=t1*DENSITY*(1-U_SQU/(2*c_squ))
    
    # nearest-neighbours
    FEQ[1,:,:]=t2*DENSITY*(1+UX/c_squ+0.5*(UX/c_squ)**2-U_SQU/(2*c_squ))
    FEQ[3,:,:]=t2*DENSITY*(1+UY/c_squ+0.5*(UY/c_squ)**2-U_SQU/(2*c_squ))
    FEQ[5,:,:]=t2*DENSITY*(1-UX/c_squ+0.5*(UX/c_squ)**2-U_SQU/(2*c_squ))
    FEQ[7,:,:]=t2*DENSITY*(1-UY/c_squ+0.5*(UY/c_squ)**2-U_SQU/(2*c_squ))
    
    # next-nearest neighbours
    FEQ[2,:,:]=t3*DENSITY*(1+U_C2/c_squ+0.5*(U_C2/c_squ)**2-U_SQU/(2*c_squ))
    FEQ[4,:,:]=t3*DENSITY*(1+U_C4/c_squ+0.5*(U_C4/c_squ)**2-U_SQU/(2*c_squ))
    FEQ[6,:,:]=t3*DENSITY*(1+U_C6/c_squ+0.5*(U_C6/c_squ)**2-U_SQU/(2*c_squ))
    FEQ[8,:,:]=t3*DENSITY*(1+U_C8/c_squ+0.5*(U_C8/c_squ)**2-U_SQU/(2*c_squ))
    
    F=omega*FEQ+(1-omega)*F
    cuda.memcpy_htod(F_gpu, F)
    
    bounceback(F_gpu, BOUNCEBACK_gpu, BOUND_gpu,
               block=(blockDimX,blockDimY,1), grid=(gridDimX,gridDimY))
    ts += 1

cuda.memcpy_dtoh(UX, UX_gpu)
cuda.memcpy_dtoh(UY, UY_gpu)

import matplotlib.pyplot as plt
plt.hold(True)
#UX *= -1
plt.quiver(UX,UY, pivot='middle')
plt.imshow(BOUND)
plt.show()