import cvxpy as cp
import numpy as np
from pathos.multiprocessing import Pool
from math import sin, cos, tan, inf
import time

pool = None

class RDA_solver:
    def __init__(self, receding, car_tuple, obstacle_list, iter_num=2, step_time=0.1, iter_threshold=0.2, process_num=4, **kwargs) -> None:

        # setting
        self.T = receding
        self.car_tuple = car_tuple # car_tuple: 'G h cone wheelbase max_speed max_acce'
        self.L = car_tuple.wheelbase
        self.max_speed = np.c_[self.car_tuple.max_speed]
        self.obstacle_list = obstacle_list
        self.iter_num = iter_num
        self.dt = step_time
        self.acce_bound = np.c_[car_tuple.max_acce] * self.dt 
        self.iter_threshold = iter_threshold

        # independ variable
        self.definition(obstacle_list)

        # flag
        # self.init_flag = True
        self.process_num = process_num

        if process_num == 1:
            self.prob_su, self.prob_LamMuZ_list = self.construct_problem(**kwargs)
        elif process_num > 1:
            global pool 
            self.prob_su = self.update_su_prob(**kwargs)
            pool = Pool(processes=process_num, initializer=self.init_prob_LamMuZ, initargs=(kwargs, )) 


    def init_prob_LamMuZ(self, kwargs):
        global prob_LamMuZ_list, para_xi_list, para_zeta_list, para_s, para_rot_list, para_dis

        para_xi_list = self.para_xi_list
        para_zeta_list = self.para_zeta_list
        para_s = self.para_s
        para_rot_list = self.para_rot_list
        para_dis = self.para_dis

        prob_LamMuZ_list = self.update_LamMuZ_prob_parallel(para_xi_list, para_zeta_list, para_s, para_rot_list, para_dis, **kwargs)

    def definition(self, obstacle_list):
        self.state_variable_define()
        self.obstacle_variable_define(obstacle_list)
        
        self.state_parameter_define()
        self.obstacle_parameter_define(obstacle_list)

        self.adjust_parameter()

    def obstacle_variable_define(self, obstacle_list):

        self.obstacle_list = obstacle_list

        # decision variables
        self.indep_lam_list = [ cp.Variable((obs.A.shape[0], self.T+1), name='lam_'+ str(obs_index)) for obs_index, obs in enumerate(obstacle_list)]
        self.indep_mu_list = [ cp.Variable((self.car_tuple.G.shape[0], self.T+1), name='mu_'+ str(obs_index)) for obs_index, obs in enumerate(obstacle_list) ]
        self.indep_z_list = [ cp.Variable((1, self.T), nonneg=True, name='z_'+ str(obs_index) ) for obs_index, obs in enumerate(obstacle_list) ]
        
    def state_variable_define(self):
        # decision variables
        self.indep_s = cp.Variable((3, self.T+1), name='state')
        self.indep_u = cp.Variable((2, self.T), name='vel')
        self.indep_dis = cp.Variable((1, self.T), name='distance', nonneg=True)

        self.indep_rot_list = [cp.Variable((2, 2), name='rot_'+str(t))  for t in range(self.T)]

    def state_parameter_define(self):
        
        self.para_ref_s = cp.Parameter((3, self.T+1), name='para_ref_state')
        self.para_ref_speed = cp.Parameter(name='para_ref_state')

        self.para_s = cp.Parameter((3, self.T+1), name='para_state')
        self.para_u = cp.Parameter((2, self.T), name='para_vel')
        self.para_dis = cp.Parameter((1, self.T), nonneg=True, value=np.ones((1, self.T)), name='para_dis')

        self.para_rot_list = [cp.Parameter((2, 2), name='para_rot_'+str(t)) for t in range(self.T)]
        self.para_drot_list = [cp.Parameter((2, 2), name='para_drot_'+str(t)) for t in range(self.T)]
        self.para_drot_phi_list = [cp.Parameter((2, 2), name='para_drot_phi_'+str(t)) for t in range(self.T)]

        self.para_A_list = [ cp.Parameter((3, 3), name='para_A_'+str(t)) for t in range(self.T)]
        self.para_B_list = [ cp.Parameter((3, 2), name='para_B_'+str(t)) for t in range(self.T)]
        self.para_C_list = [ cp.Parameter((3, 1), name='para_C_'+str(t)) for t in range(self.T)]

    def obstacle_parameter_define(self, obstacle_list):

        self.para_lam_list =  [ cp.Parameter((obs.A.shape[0], self.T+1), value=0.1*np.ones((obs.A.shape[0], self.T+1)), name='para_lam_'+str(obs_index)) for obs_index, obs in enumerate(obstacle_list) ]
        self.para_mu_list = [ cp.Parameter((self.car_tuple.G.shape[0], self.T+1), value=np.ones((self.car_tuple.G.shape[0], self.T+1)), name='para_mu_'+str(obs_index)) for obs_index, obs in enumerate(obstacle_list) ]
        self.para_z_list = [ cp.Parameter((1, self.T), nonneg=True, value=0.01*np.ones((1, self.T)), name='para_z_'+str(obs_index)) for obs_index, obs in enumerate(obstacle_list) ]

        self.para_xi_list = [ cp.Parameter((self.T+1, 2), value=np.zeros((self.T+1, 2)), name='para_xi_'+str(obs_index)) for obs_index, obs in enumerate(obstacle_list)] 
        self.para_zeta_list = [ cp.Parameter((1, self.T), value = np.zeros((1, self.T)), name='para_zeta_'+str(obs_index)) for obs_index, obs in enumerate(obstacle_list)]

    def adjust_parameter(self):
        # self.para_ws = cp.Parameter(value=1, nonneg=True)
        # self.para_wu = cp.Parameter(value=1, nonneg=True)
        self.para_slack_gain = cp.Parameter(value=10, nonneg=True)
        self.para_max_sd = cp.Parameter(value=1.0, nonneg=True)
        self.para_min_sd = cp.Parameter(value=0.1, nonneg=True)

    def update_adjust_parameter(self, **kwargs):
        # self.para_ws.value = kwargs.get('ws', 1)
        # self.para_wu.value = kwargs.get('wu', 1) 
        self.para_slack_gain = kwargs.get('slack_gain', 10)
        self.para_max_sd.value = kwargs.get('max_sd', 1.0)
        self.para_min_sd.value = kwargs.get('min_sd', 0.1)

    def iterative_solve(self, nom_s, nom_u, ref_states, ref_speed, **kwargs):

        start_time = time.time()
        
        self.para_ref_s.value = np.hstack(ref_states)[0:3, :]
        self.para_ref_speed.value = ref_speed

        self.update_state_parameter(nom_s, nom_u, self.para_dis.value)

        iteration_time = time.time()
        for i in range(self.iter_num):

            start_time = time.time()
            opt_state_array, opt_velocity_array, resi_dual, resi_pri = self.rda_solver()
            print('iteration ' + str(i) + ' time: ', time.time()-start_time)
            
            if resi_dual < self.iter_threshold and resi_pri < self.iter_threshold:
                print('iteration early stop: '+ str(i))
                break

        print('-----------------------------------------------')
        print('iteration time:', time.time() - iteration_time)
        print('==============================================')
        
        # info for debug
        opt_state_list = [state[:, np.newaxis] for state in opt_state_array.T ]
        info = {'ref_traj_list': ref_states, 'opt_state_list': opt_state_list}
        info['iteration_time'] = time.time() - start_time
        info['resi_dual'] = resi_dual
        info['resi_pri'] = resi_pri    
        
        return opt_velocity_array, info 

    def rda_solver(self):
        
        resi_dual, resi_pri = 0, 0
        
        nom_s, nom_u, nom_dis = self.su_prob_solve()

        self.update_state_parameter(nom_s, nom_u, nom_dis)

        if len(self.obstacle_list) != 0:

            LamMuZ_list, resi_dual = self.LamMuZ_prob_solve()
            self.updata_obstacle_parameter(LamMuZ_list)
                
            resi_pri = self.update_xi()
            self.update_zeta()
            
        return nom_s, nom_u, resi_dual, resi_pri

    def update_zeta(self):

        for obs_index, obs in enumerate(self.obstacle_list):
            
            lam = self.para_lam_list[obs_index].value
            mu = self.para_mu_list[obs_index].value
            z = self.para_z_list[obs_index].value
            zeta = self.para_zeta_list[obs_index].value

            Im_array = np.diag( lam.T @ obs.A @ self.para_s.value[0:2] - lam.T @ obs.b - mu.T @ self.car_tuple.h ) 
            Im_array = Im_array[np.newaxis, :]
            
            self.para_zeta_list[obs_index].value = zeta + (Im_array[0:1, 1:] - self.para_dis.value - z)    
            
    def update_xi(self): 

        hm_list = []

        for obs_index, obs in enumerate(self.obstacle_list):
            for t in range(self.T):

                lam_t = self.para_lam_list[obs_index].value[:, t+1:t+2]
                mu_t = self.para_mu_list[obs_index].value[:, t+1:t+2]
                rot_t = self.para_rot_list[t].value
                xi_t = self.para_xi_list[obs_index].value[t+1:t+2, :]
                
                Hmt = mu_t.T @ self.car_tuple.G + lam_t.T @ obs.A @ rot_t
                self.para_xi_list[obs_index].value[t+1:t+2, :] = Hmt + xi_t    

                hm_list.append(Hmt)

        hm_array = np.vstack(hm_list)
        resi_pri = np.linalg.norm(hm_array)

        return resi_pri

    def su_prob_solve(self):
        self.prob_su.solve(solver=cp.ECOS, verbose=False)

        if self.prob_su.status == cp.OPTIMAL:
            return self.indep_s.value, self.indep_u.value, self.indep_dis.value
        else:
            print('No update of state and control vector')
            return self.para_s.value, self.para_u.value, self.para_dis.value

    def LamMuZ_prob_solve(self):
        
        input_args = []
        if self.process_num > 1:
            for obs_index in range(len(self.obstacle_list)):

                nom_s = self.para_s.value
                nom_dis = self.para_dis.value
                nom_xi = self.para_xi_list[obs_index].value
                nom_zeta = self.para_zeta_list[obs_index].value
                receding = self.T
                nom_lam = self.para_lam_list[obs_index].value
                nom_mu = self.para_mu_list[obs_index].value
                nom_z = self.para_z_list[obs_index].value
                
                input_args.append((obs_index, nom_s, nom_dis, nom_xi, receding, nom_lam, nom_mu, nom_z, nom_zeta))
            
            LamMuZ_list = pool.map(RDA_solver.solve_parallel, input_args)

        else:
            for obs_index in range(len(self.obstacle_list)):
                prob = self.prob_LamMuZ_list[obs_index]
                input_args.append((prob, obs_index))
            
            LamMuZ_list = list(map(self.solve_direct, input_args))
        
        # update
        if len(LamMuZ_list) != 0:
            resi_dual_list = ([LamMu[3] for LamMu in LamMuZ_list])
            resi_dual = sum(resi_dual_list) / len(resi_dual_list)
        else:
            resi_dual = 0

        return LamMuZ_list, resi_dual

    def solve_parallel(input):
        
        obs_index, nom_s, nom_dis, nom_xi, receding, nom_lam, nom_mu, nom_z, nom_zeta = input

        prob = prob_LamMuZ_list[obs_index]

        # update parameter
        para_s.value = nom_s
        para_dis.value = nom_dis
        para_xi_list[obs_index].value = nom_xi
        para_zeta_list[obs_index].value = nom_zeta

        for t in range(receding):
            nom_st = nom_s[:, t:t+1]
            nom_phi = nom_st[2, 0]
            para_rot_list[t].value = np.array([[cos(nom_phi), -sin(nom_phi)],  [sin(nom_phi), cos(nom_phi)]])
      
        prob.solve(solver=cp.ECOS)

        for variable in prob.variables():
            if 'lam_' in variable.name():
                indep_lam_value = variable.value
            elif 'mu_' in variable.name():
                indep_mu_value = variable.value
            elif 'z_' in variable.name():
                indep_z_value = variable.value
                
        if prob.status == cp.OPTIMAL:

            lam_diff = np.linalg.norm(indep_lam_value - nom_lam)
            mu_diff = np.linalg.norm(indep_mu_value - nom_mu)
            
            z_diff = np.linalg.norm(indep_z_value - nom_z)
            residual = lam_diff**2 + mu_diff**2 + z_diff**2

            return indep_lam_value, indep_mu_value, indep_z_value, residual

        else:
            print('Update Lam Mu Fail')
            return nom_lam, nom_mu, nom_z, inf

    def solve_direct(self, input):
        
        prob, obs_index = input
        prob.solve(solver=cp.ECOS)

        indep_lam = self.indep_lam_list[obs_index]
        indep_mu = self.indep_mu_list[obs_index]
        indep_z = self.indep_z_list[obs_index]

        para_lam = self.para_lam_list[obs_index]
        para_mu = self.para_mu_list[obs_index]
        para_z = self.para_z_list[obs_index]

        if prob.status == cp.OPTIMAL:

            lam_diff = np.linalg.norm(indep_lam.value - para_lam.value)
            mu_diff = np.linalg.norm(indep_mu.value - para_mu.value)
            z_diff = np.linalg.norm(indep_z.value - para_z.value)
            residual = lam_diff**2 + mu_diff**2 + z_diff**2

            return indep_lam.value, indep_mu.value, indep_z.value, residual
        else:
            print('Update Lam Mu Fail')
            return para_lam.value, para_mu.value, para_z.value, inf
    
    def update_state_parameter(self, nom_s, nom_u, nom_dis):

        self.para_s.value = nom_s
        self.para_u.value = nom_u
        self.para_dis.value = nom_dis
        
        for t in range(self.T):
            nom_st = nom_s[:, t:t+1]
            nom_ut = nom_u[:, t:t+1]
            
            A, B, C = self.linear_ackermann_model(nom_st, nom_ut, self.dt, self.L)

            self.para_A_list[t].value = A
            self.para_B_list[t].value = B
            self.para_C_list[t].value = C

            nom_phi = nom_st[2, 0]
            self.para_rot_list[t].value = np.array([[cos(nom_phi), -sin(nom_phi)],  [sin(nom_phi), cos(nom_phi)]])
            self.para_drot_list[t].value = np.array( [[-sin(nom_phi), -cos(nom_phi)],  [cos(nom_phi), -sin(nom_phi)]] )
            self.para_drot_phi_list[t].value = nom_phi * np.array( [[-sin(nom_phi), -cos(nom_phi)],  [cos(nom_phi), -sin(nom_phi)]] )

    def update_state_parameter_parallel(self, input):

        nom_s, nom_dis = input

        para_s.value = nom_s
        para_dis.value = nom_dis
        
        for t in range(self.T):
            nom_st = nom_s[:, t:t+1]
            
            nom_phi = nom_st[2, 0]
            para_rot_list[t].value = np.array([[cos(nom_phi), -sin(nom_phi)],  [sin(nom_phi), cos(nom_phi)]])

    def updata_obstacle_parameter(self, LamMuZ_list):

        for index, LamMuZ in enumerate(LamMuZ_list):
            self.para_lam_list[index].value = LamMuZ[0]
            self.para_mu_list[index].value = LamMuZ[1]
            self.para_z_list[index].value = LamMuZ[2]
            
    def construct_problem(self, **kwargs):
        prob_su = self.update_su_prob(**kwargs)
        prob_LamMuZ_list = self.update_LamMuZ_prob(**kwargs)

        return prob_su, prob_LamMuZ_list
    
    def update_LamMuZ_prob_parallel(self, para_xi_list, para_zeta_list, para_s, para_rot_list, para_dis, **kwargs):

        ro1 = kwargs.get('ro1', 200)
        ro2 = kwargs.get('ro2', 1) 

        prob_list = []

        for obs_index in range(len(self.obstacle_list)):

            indep_lam = self.indep_lam_list[obs_index]
            indep_mu = self.indep_mu_list[obs_index]
            indep_z = self.indep_z_list[obs_index]

            para_xi = para_xi_list[obs_index]
            obs = self.obstacle_list[obs_index]
            para_zeta = para_zeta_list[obs_index]

            cost, constraints = self.LamMuZ_cost_cons(indep_lam, indep_mu, indep_z, para_s, para_rot_list, para_xi, para_dis, para_zeta, obs, self.T, ro1, ro2)
            
            prob = cp.Problem(cp.Minimize(cost), constraints)
            prob_list.append(prob)

        return prob_list
    

    def update_LamMuZ_prob(self, **kwargs):
        
        ro1 = kwargs.get('ro1', 200)
        ro2 = kwargs.get('ro2', 1) 
        prob_list = []

        for obs_index in range(len(self.obstacle_list)):

            indep_lam = self.indep_lam_list[obs_index]
            indep_mu = self.indep_mu_list[obs_index]
            indep_z = self.indep_z_list[obs_index]

            para_xi = self.para_xi_list[obs_index]
            obs = self.obstacle_list[obs_index]
            para_zeta = self.para_zeta_list[obs_index]

            cost, constraints = self.LamMuZ_cost_cons(indep_lam, indep_mu, indep_z, self.para_s, self.para_rot_list, para_xi, self.para_dis, para_zeta, obs, self.T, ro1, ro2)
            
            prob = cp.Problem(cp.Minimize(cost), constraints)

            assert prob.is_dcp(dpp=True)
            
            prob_list.append(prob)

        return prob_list

    
    def update_su_prob(self, **kwargs):
        
        self.update_adjust_parameter(**kwargs)

        ws = kwargs.get('ws', 1)
        wu = kwargs.get('ws', 1)

        ro1 = kwargs.get('ro1', 200)
        ro2 = kwargs.get('ro2', 1)
        
        nav_cost, nav_constraints = self.nav_cost_cons(ws, wu)
        su_cost, su_constraints = self.update_su_cost_cons(self.para_slack_gain, ro1, ro2)

        prob_su = cp.Problem(cp.Minimize(nav_cost+su_cost), su_constraints+nav_constraints) 

        assert prob_su.is_dcp(dpp=True)

        return prob_su
    
    def nav_cost_cons(self, ws=1, wu=1):
 
        # path tracking objective cost constraints
        # indep_s: cp.Variable((3, self.receding+1), name='state')
        # indep_u: cp.Variable((2, self.receding), name='vel')
        # para_ref_s: cp.Parameter((3, self.T+1), name='para_ref_state')

        cost = 0
        constraints = []

        cost += self.C0_cost(self.para_ref_s, self.para_ref_speed, self.indep_s, self.indep_u[0, :], ws, wu)

        constraints += self.dynamics_constraint(self.indep_s, self.indep_u, self.T)
        constraints += self.bound_su_constraints(self.indep_s, self.indep_u, self.para_s, self.max_speed, self.acce_bound)

        return cost, constraints

    def update_su_cost_cons(self, slack_gain=10, ro1=200, ro2=1):
        cost = 0
        constraints = []

        if len(self.obstacle_list) == 0:
            return cost, constraints

        cost += self.C1_cost(self.indep_dis, slack_gain)

        Im_su_list = []
        Hm_su_list = []
        
        for obs_index, obs in enumerate(self.obstacle_list):  
            
            para_xi = self.para_xi_list[obs_index]

            para_lam = self.para_lam_list[obs_index]
            para_mu = self.para_mu_list[obs_index]
            para_z = self.para_z_list[obs_index]
            para_zeta = self.para_zeta_list[obs_index]

            Imsu = self.Im_su(self.indep_s, self.indep_dis, para_lam, para_mu, para_z, para_zeta, obs)
            Hmsu = self.Hm_su(self.indep_rot_list, para_mu, para_lam, para_xi, obs, self.T)
            
            Im_su_list.append(Imsu)
            Hm_su_list.append(Hmsu)

        rot_diff_list = []
        for t in range(self.T):

            indep_phi = self.indep_s[2, t+1:t+2]
            indep_rot_t = self.indep_rot_list[t]

            rot_diff_list.append(self.para_rot_list[t] - self.para_drot_phi_list[t] + self.para_drot_list[t] * indep_phi - indep_rot_t)

        rot_diff_array = cp.vstack(rot_diff_list)
        Im_su_array = cp.vstack(Im_su_list)
        Hm_su_array = cp.vstack(Hm_su_list)

        constraints += [cp.constraints.zero.Zero(rot_diff_array)]
        
        cost += 0.5*ro1 * cp.sum_squares(cp.neg(Im_su_array))
        # constraints += [Im_su_array >= 0]
        cost += 0.5*ro2 * cp.sum_squares(Hm_su_array)

        constraints += self.bound_dis_constraints(self.indep_dis)

        return cost, constraints

    def LamMuZ_cost_cons(self, indep_lam, indep_mu, indep_z, para_s, para_rot_list, para_xi, para_dis, para_zeta, obs, receding, ro1, ro2):

        cost = 0
        constraints = []

        Hm_array = self.Hm_LamMu(indep_lam, indep_mu, para_rot_list, para_xi, obs, receding)
        Im_array = self.Im_LamMu(indep_lam, indep_mu, indep_z, para_s, para_dis, para_zeta, obs)

        cost += 0.5*ro1 * cp.sum_squares(cp.neg(Im_array))
        # constraints += [ Im_array >= 0 ]
        cost += 0.5*ro2 * cp.sum_squares(Hm_array)

        constraints += [ cp.norm(obs.A.T @ indep_lam, axis=0) <= 1 ]
        constraints += [ self.cone_cp_array(-indep_lam, obs.cone_type) ]
        constraints += [ self.cone_cp_array(-indep_mu, self.car_tuple.cone_type) ]

        return cost, constraints


    def Im_su(self, state, distance, para_lam, para_mu, para_z, para_zeta, obs):
        
        indep_trans = state[0:2]
        Im_array = cp.diag( para_lam.T @ obs.A @ indep_trans - para_lam.T @ obs.b - para_mu.T @ self.car_tuple.h)

        return Im_array[1:] - distance[0, :] - para_z[0, :] + para_zeta[0, :]

    def Hm_su(self, rot, para_mu, para_lam, para_xi, obs, receding):
        
        Hm_list = []

        for t in range(receding):
    
            lam_t = para_lam[:, t+1:t+2]
            mu_t = para_mu[:, t+1:t+2]
            para_xi_t = para_xi[t+1:t+2, :]
            indep_rot_t = rot[t]

            Hmt = mu_t.T @ self.car_tuple.G + lam_t.T @ obs.A @ indep_rot_t + para_xi_t

            Hm_list.append(Hmt)

        return cp.vstack(Hm_list)

    def Hm_LamMu(self, indep_lam, indep_mu, para_rot_list, para_xi, obs, receding):

        Hm_list = []
        for t in range(receding):
            lam_t = indep_lam[:, t+1:t+2]
            mu_t = indep_mu[:, t+1:t+2]
            
            para_rot_t = para_rot_list[t]
            para_xi_t = para_xi[t+1:t+2, :]
        
            Hmt = mu_t.T @ self.car_tuple.G + lam_t.T @ obs.A @ para_rot_t + para_xi_t
            Hm_list.append(Hmt)

        return cp.vstack(Hm_list)

    def Im_LamMu(self, indep_lam, indep_mu, indep_z, para_s, para_dis, para_zeta, obs):

        Im_array = cp.diag( indep_lam.T @ obs.A @ para_s[0:2] - indep_lam.T @ obs.b - indep_mu.T @ self.car_tuple.h ) 
        Im = Im_array[1:] - para_dis[0, :] - indep_z[0, :] + para_zeta[0, :]

        return Im


    def dynamics_constraint(self, state, control_u, receding):

        temp_s1_list = []

        for t in range(receding):
            indep_st = state[:, t:t+1]
            indep_ut = control_u[:, t:t+1]

            ## dynamic constraints
            A = self.para_A_list[t]
            B = self.para_B_list[t]
            C = self.para_C_list[t]
            
            temp_s1_list.append(A @ indep_st + B @ indep_ut + C)
        
        constraints = [ state[:, 1:] == cp.hstack(temp_s1_list) ]

        return constraints
        
    def bound_su_constraints(self, state, control_u, para_s, max_speed, acce_bound):

        constraints = []

        constraints += [ cp.abs(control_u[:, 1:] - control_u[:, :-1] ) <= acce_bound ]  # constraints on speed accelerate
        constraints += [ cp.abs(control_u) <= max_speed]
        constraints += [ state[:, 0:1] == para_s[:, 0:1] ]

        return constraints

    def bound_dis_constraints(self, indep_dis):

        constraints = []

        constraints += [ cp.max(indep_dis) <= self.para_max_sd ] 
        constraints += [ cp.min(indep_dis) >= self.para_min_sd ]

        return constraints


    def linear_ackermann_model(self, nom_state, nom_u, dt, L):
        
        phi = nom_state[2, 0]
        v = nom_u[0, 0]
        psi = nom_u[1, 0]

        A = np.array([ [1, 0, -v * dt * sin(phi)], [0, 1, v * dt * cos(phi)], [0, 0, 1] ])

        B = np.array([ [cos(phi)*dt, 0], [sin(phi)*dt, 0], 
                        [ tan(psi)*dt / L, v*dt/(L * (cos(psi))**2 ) ] ])

        C = np.array([ [ phi*v*sin(phi)*dt ], [ -phi*v*cos(phi)*dt ], 
                        [ -psi * v*dt / ( L * (cos(psi))**2) ]])

        return A, B, C

    def C0_cost(self, ref_s, ref_speed, state, speed, ws, wu):

        diff_s = (state - ref_s)
        diff_u = (speed - ref_speed)

        return ws * cp.sum_squares(diff_s) + wu*cp.sum_squares(diff_u) 

    def C1_cost(self, indep_dis, slack_gain):
        return ( -slack_gain * cp.sum(indep_dis) )

    def cone_cp_array(self, array, cone='Rpositive'):
        # cone for cvxpy: R_positive, norm2
        if cone == 'Rpositive':
            return cp.constraints.nonpos.NonPos(array)
        elif cone == 'norm2':
            return cp.constraints.nonpos.NonPos( cp.norm(array[0:-1], axis = 0) - array[-1]  )

