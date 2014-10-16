# -*- coding: utf-8 -*-
"""
Created on Tue Aug  5 12:10:08 2014

@author: ibackus
"""

# Multiprocessing modules
from multiprocessing import Pool, cpu_count
import multiprocessing as mp

# Generic pacakges
import numpy as np
import pynbody
SimArray = pynbody.array.SimArray
import subprocess
import glob
import os

# 'Internal' packages
import isaac


print 'ok'


def calc_clump_pars(f, clump_nums):
    
    if isinstance(f, str):
        
        f = pynbody.load(f)
        
    mask1 = clump_nums > 0
    
    clump_nums2 = clump_nums[mask1]
    # Get units set up
    m_unit = f['mass'].units
    l_unit = f['pos'].units
    v_unit = f['vel'].units
    rho_unit = f.g['rho'].units
    
    # Get arrays of pointers to the required quantities
    f_mass = f['mass'][mask1]
    f_pos = f['pos'][mask1]
    f_v = f['vel'][mask1]
    f_T = f['temp'][mask1]
    
    n_gas = len(f.g[mask1])
    n_use = mask1.sum()
    f_rho = SimArray(np.zeros(n_use), rho_unit)
    f_rho[0:n_gas] = f.g['rho']
    
    # Initialize arrays
    n_clumps = clump_nums2.max()
    
    m = SimArray(np.zeros(n_clumps), m_unit) # clump mass
    N = np.zeros(n_clumps, dtype=int) # Number of particles/clump
    pos = SimArray(np.zeros([n_clumps,3]), l_unit) # center of mass
    r = SimArray(np.zeros(n_clumps), l_unit) # center of mass radial position
    v = SimArray(np.zeros([n_clumps, 3]), v_unit) # center of mass velocity
    # Angular momentum around the center of mass rest frame
    J = SimArray(np.zeros([n_clumps, 3]), m_unit*l_unit*v_unit)
    T = SimArray(np.zeros(n_clumps), 'K') # mass averaged temperature
    rho = SimArray(np.zeros(n_clumps), rho_unit) # density
    r_clump = SimArray(np.zeros(n_clumps), l_unit) # clump radius (size)
    
    # index of each particle
    particle_ids = []
    
    for i in range(n_clumps):
        
        mask2 = (clump_nums2 == i+1)
        
        # Mask the input arrays to look at only the current clump
        p_mass = f_mass[mask2]
        p_pos = f_mass[mask2]
        p_v = f_v[mask2]
        p_T = f_T[mask2]
        p_rho = f_rho[mask2]
        
        # Calculate properties of the clump
        N[i] = mask2.sum()
        m[i] = p_mass.sum()
        pos[i] = np.dot(p_pos.T, p_mass[:,None])/float(m[i])
        r[i] = np.sqrt((pos[i]**2).sum())
        v[i] = np.dot(p_v.T, p_mass[:,None])/float(m[i])
        
        
    return N, m, pos, r, v
        

def _parallel_find_clumps(args):
    """
    A wrapper to parallelize find_clumps()
    """    
    return find_clumps(*args)
    
def batch_clumps2(f_list, n_smooth=32, param=None, arg_string=None):
    """
    A parallel implementation of find_clumps.  Since SKID is not parallelized
    this can be used to run find_clumps on a set of snapshots from one
    simulation.
    
    **ARGUMENTS**
    
    f_list : list
        A list containing the filenames of snapshots OR the tipsy snapshots
        
    **RETURNS**
    
    clumps : list
        A list containing the clumps for each snapshot in f_list
    """
    
    n_proc = cpu_count()
    #n_files = len(f_list)
    
    arg_list = []
    
    for i, f_name in enumerate(f_list):
        
        arg_list.append([f_name, n_smooth, param, arg_string, i])
        
    print arg_list
    # Set up the pool
    pool = Pool(n_proc)
    
    # Run the job in parallel
    results = pool.map(_parallel_find_clumps, arg_list)
    pool.close()
    
    return results

def find_clumps(f, n_smooth=32, param=None, arg_string=None, seed=None):
    """
    Uses skid (https://github.com/N-BodyShop/skid) to find clumps in a gaseous
    protoplanetary disk.  
    
    The linking length used is equal to the gravitational softening length of
    the gas particles.
    
    The density cut-off comes from the criterion that there are n_smooth particles
    within the Hill sphere of a particle.  This is formulated mathematically as:
    
        rho_min = 3*n_smooth*Mstar/R^3
        
    where R is the distance from the star.  The trick used here is to multiply
    all particle masses by R^3 before running skid so the density cut-off is:
    
        rho_min = 3*n_smooth*Mstar
        
    **ARGUMENTS**
    
    *f* : TipsySnap, or str
        A tipsy snapshot loaded/created by pynbody -OR- a filename pointing to a
        snapshot.
    
    *n_smooth* : int (optional)
        Number of particles used in SPH calculations.  Should be the same as used
        in the simulation.  Default = 32
    
    *param* : dict (optional)
        param dictionary (see isaac.configparser)
    
    *arg_string* : str (optional)
        Additional arguments to be passed to skid.  Cannot use -tau, -d, -m, -s, -o
    
    *seed* : int
        An integer used to seed the random filename generation for temporary
        files.  Necessary for multiprocessing and should be unique for each
        thread.
    
    **RETURNS**
    
    *clumps* : array, int-like
        Array containing the group number each particle belongs to, with star
        particles coming after gas particles.  A zero means the particle belongs
        to no groups
    """
    # Parse areguments
    if isinstance(f, str):
        
        f = pynbody.load(f)
        
    if seed is not None:
        
        np.random.seed(seed)
        
    # Estimate the linking length as the gravitational softening length
    tau = f.g['eps'][0]
    
    # Calculate minimum density
    rho_min = 3*n_smooth*f.s['mass'][0]
    
    # Center on star.  This is done because R used in hill-sphere calculations
    # is relative to the star
    star_pos = f.s['pos'].copy()
    f['pos'] -= star_pos
    
    # Scale mass by R^3
    R = isaac.strip_units(f['rxy'])
    m0 = f['mass'].copy()
    f['mass'] *= (R+tau)**3
    
    # Save temporary snapshot
    f_prefix = str(np.random.randint(np.iinfo(int).max))
    f_name = f_prefix + '.std'
    
    if param is not None:
        
        param_name = f_prefix + '.param'
        isaac.configsave(param, param_name)
        
    f.write(filename=f_name, fmt=pynbody.tipsy.TipsySnap)
        
    f['pos'] += star_pos
    f['mass'] = m0
    
    command = 'totipnat < {} | skid -tau {:.2e} -d {:.2e} -m {:d} -s {:d} -o {}'\
    .format(f_name, tau, rho_min, n_smooth, n_smooth, f_prefix)
    print '\n', command
    #p = subprocess.Popen(command, shell=True, stdout=logfile, stderr=logfile)
    p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    for line in iter(p.stdout.readline, ''):
        print line,
    p.wait()
    
    # Load clumps
    clumps = isaac.loadhalos(f_prefix + '.grp')
    
    # Cleanup
    for name in glob.glob(f_prefix + '*'):
        
        os.remove(name)
        
    return clumps