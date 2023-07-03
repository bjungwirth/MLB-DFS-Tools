import csv
import json
import math
import os
import random
import time
import numpy as np
import pulp as plp
import multiprocessing as mp
import pandas as pd
import statistics
#import fuzzywuzzy
import itertools
import collections
import re

class MLB_GPP_Simulator:
    config = None
    player_dict = {}
    field_lineups = {}
    stacks_dict = {}
    gen_lineup_list = []
    roster_construction = []
    id_name_dict = {}
    salary = None
    optimal_score = None
    field_size = None
    team_list = []
    num_iterations = None
    site = None
    payout_structure = {}
    use_contest_data = False
    entry_fee = None
    use_lineup_input = None
    projection_minimum = 15
    randomness_amount = 100
    min_lineup_salary = 48000
    max_pct_off_optimal = 0.4

    def __init__(
        self,
        site,
        field_size,
        num_iterations,
        use_contest_data,
        use_lineup_input,
        match_lineup_input_to_field_size,
    ):
        self.site = site
        self.use_lineup_input = use_lineup_input
        self.match_lineup_input_to_field_size = match_lineup_input_to_field_size
        self.load_config()
        self.load_rules()
        
        projection_path = os.path.join(
            os.path.dirname(__file__),
            "../{}_data/{}".format(site, self.config["projection_path"]),
        )
        self.load_projections(projection_path)
        
        player_path = os.path.join(
            os.path.dirname(__file__),
            "../{}_data/{}".format(site, self.config["player_path"]),
        )
        self.load_player_ids(player_path)

        ownership_path = os.path.join(
            os.path.dirname(__file__),
            "../{}_data/{}".format(site, self.config["ownership_path"]),
        )
        self.load_ownership(ownership_path)

        boom_bust_path = os.path.join(
            os.path.dirname(__file__),
            "../{}_data/{}".format(site, self.config["boom_bust_path"]),
        )
        self.load_boom_bust(boom_bust_path)
        
        stacks_path = os.path.join(
            os.path.dirname(__file__),
            "../{}_data/{}".format(site, self.config["team_stacks_path"]),
        )        
        self.load_team_stacks(stacks_path)
        
 #       batting_order_path = os.path.join(
 #           os.path.dirname(__file__),
#            "../{}_data/{}".format(site, self.config["batting_order_path"]),
#        )                
#        self.load_batting_order(batting_order_path)

        if site == "dk":
            self.roster_construction = ["P", "P","C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"]
            self.salary = 50000
        
        elif site == "fd":
            self.roster_construction = ["P", "C/1B", "2B", "3B", "SS", "OF", "OF", "OF", "UTIL"]
            self.salary = 60000

        self.use_contest_data = use_contest_data
        if use_contest_data:
            contest_path = os.path.join(
                os.path.dirname(__file__),
                "../{}_data/{}".format(site, self.config["contest_structure_path"]),
            )
            self.load_contest_data(contest_path)
            print("Contest payout structure loaded.")
        else:
            self.field_size = int(field_size)
            self.payout_structure = {0: 0.0}
            self.entry_fee = 0
        
        self.adjust_default_stdev()
        self.num_iterations = int(num_iterations)
        self.get_optimal()
        if self.use_lineup_input:
            self.load_lineups_from_file()
        if self.match_lineup_input_to_field_size or len(self.field_lineups) == 0:
            self.generate_field_lineups()
    
    #make column lookups on datafiles case insensitive
    def lower_first(self, iterator):
        return itertools.chain([next(iterator).lower()], iterator)

    def load_rules(self):
        self.projection_minimum = int(self.config["projection_minimum"])
        self.randomness_amount = float(self.config["randomness"])
        self.min_lineup_salary = int(self.config["min_lineup_salary"])
        self.max_pct_off_optimal = float(self.config["max_pct_off_optimal"])
        self.pct_field_using_stacks = float(self.config['pct_field_using_stacks'])
        self.default_hitter_var = float(self.config['default_hitter_var'])
        self.default_pitcher_var = float(self.config['default_pitcher_var'])
        self.pct_5man_stacks = float(self.config['pct_5man_stacks'])
        self.overlap_limit = float(self.config['num_hitters_vs_pitcher'])

    # In order to make reasonable tournament lineups, we want to be close enough to the optimal that
    # a person could realistically land on this lineup. Skeleton here is taken from base `mlb_optimizer.py`
    def get_optimal(self):
        for p,s in self.player_dict.items():
            if s["ID"]==0:
                print(s["Name"])
        problem = plp.LpProblem('MLB', plp.LpMaximize)
        lp_variables = {self.player_dict[(player, pos_str, team)]['ID']: plp.LpVariable(
            str(self.player_dict[(player, pos_str, team)]['ID']), cat='Binary') for (player, pos_str, team) in self.player_dict}

        # set the objective - maximize fpts
        problem += plp.lpSum(self.player_dict[(player, pos_str, team)]['Fpts'] * lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                             for (player, pos_str, team) in self.player_dict), 'Objective'

        # Set the salary constraints
        problem += plp.lpSum(self.player_dict[(player, pos_str, team)]['Salary'] * lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                             for (player, pos_str, team) in self.player_dict) <= self.salary

        if self.site == 'dk':
            # Need 2 pitchers
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict if 'P' in self.player_dict[(player, pos_str, team)]['Position']) == 2
            # Need 1 catcher
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict if 'C' in self.player_dict[(player, pos_str, team)]['Position']) == 1
            # Need 1 first baseman
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict if '1B' in self.player_dict[(player, pos_str, team)]['Position']) == 1
            # Need at least 1 power forward, can have up to 3 if utilizing F and UTIL slots
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict  if '2B' in self.player_dict[(player, pos_str, team)]['Position']) == 1
            # Need at least 1 center, can have up to 2 if utilizing C and UTIL slots
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict  if '3B' in self.player_dict[(player, pos_str, team)]['Position']) == 1
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict if 'SS' in self.player_dict[(player, pos_str, team)]['Position']) == 1
            # Need 3 outfielders
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict if 'OF' in self.player_dict[(player, pos_str, team)]['Position'])  == 3
            # Can only roster 8 total players
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict) == 10
            
                        # Max 5 hitters per team
            for team in self.team_list:
                problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict if (self.player_dict[(player, pos_str, team)]['Team'] == team & self.player_dict[(player, pos_str, team)]['Position']!='P')) <= 5
                
        else:
            # Need 2 pitchers
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict if 'P' in self.player_dict[(player, pos_str, team)]['Position']) == 1
            # Need 1 catcher or first baseman
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict if 'C' in self.player_dict[(player, pos_str, team)]['Position']) == 1

            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict if '1B' in self.player_dict[(player, pos_str, team)]['Position']) == 1
            # Need 1 second baseman 
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict if '2B' in self.player_dict[(player, pos_str, team)]['Position']) == 1
            # Need 1 third baseman
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict if '3B' in self.player_dict[(player, pos_str, team)]['Position']) == 1
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict  if 'SS' in self.player_dict[(player, pos_str, team)]['Position']) == 1
            # Need 3 outfielders
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict  if 'OF' in self.player_dict[(player, pos_str, team)]
                                 ['Position'])  == 3

            # Need 1 UTIL 
            
            # Can only roster 8 total players
            problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict ) == 10
            
                        # Max 5 hitters per team
            for team in self.team_list:
                problem += plp.lpSum(lp_variables[self.player_dict[(player, pos_str, team)]['ID']]
                                 for (player,pos_str,team) in self.player_dict  if (self.player_dict[(player, pos_str, team)]['Team'] == team & self.player_dict[(player, pos_str, team)]['Position']!='P')) <= 5
                
       # Crunch!
        try:
            problem.solve(plp.PULP_CBC_CMD(msg=0))
        except plp.PulpSolverError:
            print('Infeasibility reached - only generated {} lineups out of {}. Continuing with export.'.format(
                len(self.num_lineups), self.num_lineups))

        score = str(problem.objective)
        for v in problem.variables():
            score = score.replace(v.name, str(v.varValue))

        self.optimal_score = eval(score)
        
    # Load player IDs for exporting
    def load_player_ids(self, path):
        with open(path, encoding="utf-8-sig") as file:
            reader = csv.DictReader(self.lower_first(file))
            for row in reader:
                name_key = "name" if self.site == "dk" else "nickname"
                player_name = row[name_key].replace("-", "#").lower()
                if 'P' in row['position']:
                    row['position'] = 'P'
                # some players have 2 positions - will be listed like 'PG/SF' or 'PF/C'
                position = [pos for pos in row['position'].split('/')]
                if row['teamabbrev'] == 'WSH':
                    team = 'WAS'
                else:
                    team = row['teamabbrev']
                match =  re.search(pattern='(\w{2,4}@\w{2,4})', string=row['game info'])
                opp = ''
                match = match.groups()[0].split('@')
                for m in match:
                    m = m.strip()
                    if m == 'WSH':
                        m = 'WAS'
                    if m != team:
                        opp = m
                  # adjust "opponent" to match your CSV column
                pos_str = str(position)
                if (player_name,pos_str, team) in self.player_dict:
                    self.player_dict[(player_name,pos_str, team)]["ID"] = str(row["id"])
                    self.player_dict[(player_name,pos_str, team)]["Team"] =  row["teamabbrev"]
                    self.player_dict[(player_name,pos_str, team)]["Opp"] = opp
                #else:
                #    print(row[name_key] + ' not found in projections!')
                self.id_name_dict[str(row["id"])] = row[name_key]
                    
    def load_contest_data(self, path):
        with open(path, encoding="utf-8-sig") as file:
            reader = csv.DictReader(self.lower_first(file))
            for row in reader:
                if self.field_size is None:
                    self.field_size = int(row["field size"])
                if self.entry_fee is None:
                    self.entry_fee = float(row["entry fee"])
                # multi-position payouts
                if "-" in row["place"]:
                    indices = row["place"].split("-")
                    # print(indices)
                    # have to add 1 to range to get it to generate value for everything
                    for i in range(int(indices[0]), int(indices[1]) + 1):
                        # print(i)
                        # Where I'm from, we 0 index things. Thus, -1 since Payout starts at 1st place
                        if i >= self.field_size:
                            break
                        self.payout_structure[i - 1] = float(
                            row["payout"].split(".")[0].replace(",", "")
                        )
                # single-position payouts
                else:
                    if int(row["place"]) >= self.field_size:
                        break
                    self.payout_structure[int(row["place"]) - 1] = float(
                        row["payout"].split(".")[0].replace(",", "")
                    )
        # print(self.payout_structure)

    # Load config from file
    def load_config(self):
        with open(
            os.path.join(os.path.dirname(__file__), "../config.json"),
            encoding="utf-8-sig",
        ) as json_file:
            self.config = json.load(json_file)

    # Load projections from file
    def load_projections(self, path):
        # Read projections into a dictionary
        with open(path, encoding="utf-8-sig") as file:
            reader = csv.DictReader(self.lower_first(file))
            for row in reader:
                player_name = row["name"].replace("-", "#").lower()
                if float(row["fpts"]) < self.projection_minimum:
                    continue
                if 'P' in row['pos']:
                    row['pos'] = 'P'
                # some players have 2 positions - will be listed like 'PG/SF' or 'PF/C'
                position = [pos for pos in row['pos'].split('/')]
                if row['team'] == 'WSH':
                    team = 'WAS'
                else:
                    team = row['team']
                pos_str = str(position)
                # For pitchers, set Order to None
                if row['ord'] == '-':
                    order = None
                else:
                    order = int(row["ord"])
                self.player_dict[(player_name, pos_str,team)] = {
                    "Fpts": float(row["fpts"]),
                    "Position": position,
                    "Name" : player_name,
                    "Team" : team,
                    "Opp" : '',
                    "ID": '',
                    "Salary": int(row["salary"].replace(",", "")),
                    "StdDev": 0,
                    "Ceiling": 0,
                    "Ownership": 0.1,
                    "Order": order,  # Handle blank orders
                    "In Lineup": False
                }


                                 
    # Load ownership from file
    def load_ownership(self, path):
        # Read ownership into a dictionary
        with open(path, encoding="utf-8-sig") as file:
            reader = csv.DictReader(self.lower_first(file))
            for row in reader:
                player_name = row["name"].replace("-", "#").lower()                
                position = [pos for pos in row['pos'].split('/')]
                if row['team'] == 'WSH':
                    team = 'WAS'
                else:
                    team = row['team']
                pos_str = str(position)
                if (player_name,pos_str, team) in self.player_dict:
                    self.player_dict[(player_name,pos_str, team)]["Ownership"] = float(row["own%"])

    # Load standard deviations
    def load_boom_bust(self, path):
        with open(path, encoding="utf-8-sig") as file:
            reader = csv.DictReader(self.lower_first(file))
            for row in reader:
                #print(row)
                player_name = row["name"].replace("-", "#").lower()                
                position = [pos for pos in row['pos'].split('/')]
                pos_str = str(position)
                if row['team'] == 'WSH':
                    team = 'WAS'
                else:
                    team = row['team']
                if (player_name,pos_str, team) in self.player_dict:
                    self.player_dict[(player_name,pos_str, team)]["StdDev"] = float(row["stddev"])
                    self.player_dict[(player_name,pos_str, team)]["Ceiling"] = float(row["ceiling"])
    
    def adjust_default_stdev(self):
        for (player_name,pos, team) in self.player_dict.keys():
            if self.player_dict[(player_name,pos,team)]['StdDev'] == 0:
                if self.player_dict[(player_name,pos,team)]["Position"]== ["P"]:
                    print(player_name + ' has no stddev, defaulting to ' + str(self.default_pitcher_var) + '*projection')
                    self.player_dict[(player_name,pos,team)]["StdDev"] = self.player_dict[(player_name,pos,team)]["Fpts"]*self.default_pitcher_var
                else:
                    print(player_name + ' has no stddev, defaulting to ' + str(self.default_hitter_var) + '*projection')
                    self.player_dict[(player_name,pos,team)]["StdDev"] = self.player_dict[(player_name,pos,team)]["Fpts"]*self.default_hitter_var           
                    
    def load_team_stacks(self,path):
        with open(path) as file:
            reader = csv.DictReader(self.lower_first(file))
            for row in reader:
                if row['team'] == 'WSH':
                    team = 'WAS'
                else:
                    team = row['team']
                self.stacks_dict[team]= float(row["own%"])/100
                    
    def remap(self, fieldnames):
        return ["P","C/1B","2B","3B","SS","OF","OF","OF","UTIL"]

    def load_lineups_from_file(self):
        print("loading lineups")
        i = 0
        path = os.path.join(
            os.path.dirname(__file__),
            "../{}_data/{}".format(self.site, "tournament_lineups.csv"),
        )
        with open(path) as file:
            if self.site == "dk":
                reader = pd.read_csv(file)
                lineup = []
                for i, row in reader.iterrows():
                    # print(row)
                    if i == self.field_size:
                        break
                    lineup = [
                        str(row[0].split("(")[1].replace(")","")),
                        str(row[1].split("(")[1].replace(")","")),
                        str(row[2].split("(")[1].replace(")","")),
                        str(row[3].split("(")[1].replace(")","")),
                        str(row[4].split("(")[1].replace(")","")),
                        str(row[5].split("(")[1].replace(")","")),
                        str(row[6].split("(")[1].replace(")","")),
                        str(row[7].split("(")[1].replace(")","")),
                        str(row[8].split("(")[1].replace(")","")), 
                        str(row[9].split("(")[1].replace(")",""))
                    ]
                    # storing if this lineup was made by an optimizer or with the generation process in this script
                    self.field_lineups[i] = {
                        "Lineup": lineup,
                        "Wins": 0,
                        "Top10": 0,
                        "ROI": 0,
                        "Cashes": 0,
                        "Type": "opto",
                    }
                    i += 1
            elif self.site == "fd":
                reader = pd.read_csv(file)
                lineup = []
                for i, row in reader.iterrows():
                    # print(row)
                    if i == self.field_size:
                        break
                    lineup = [
                        str(row[0].split("(")[1].replace(")","")),
                        str(row[1].split("(")[1].replace(")","")),
                        str(row[2].split("(")[1].replace(")","")),
                        str(row[3].split("(")[1].replace(")","")),
                        str(row[4].split("(")[1].replace(")","")),
                        str(row[5].split("(")[1].replace(")","")),
                        str(row[6].split("(")[1].replace(")","")),
                        str(row[7].split("(")[1].replace(")","")),
                        str(row[8].split("(")[1].replace(")","")) 
                    ]
                    # storing if this lineup was made by an optimizer or with the generation process in this script
                    self.field_lineups[i] = {
                        "Lineup": lineup,
                        "Wins": 0,
                        "Top10": 0,
                        "ROI": 0,
                        "Cashes": 0,
                        "Type": "opto",
                    }
                    i += 1                
        #print(self.field_lineups)

    @staticmethod
    def generate_lineups(
        lu_num,
        ids,
        in_lineup,
        pos_matrix,
        ownership,
        salary_floor,
        salary_ceiling,
        optimal_score,
        salaries,
        projections,
        max_pct_off_optimal,
        teams,
        opponents,
        team_stack,
        stack_len,
        overlap_limit
    ):
        # new random seed for each lineup (without this there is a ton of dupes)
        np.random.seed(lu_num)
        lus = {}
        # make sure nobody is already showing up in a lineup
        if sum(in_lineup) != 0:
            in_lineup.fill(0)
        reject = True
        if pos_matrix.shape[1] == 10:
            num_p_on_roster = 2
        else:
            num_p_on_roster = 1
        while reject:
            if team_stack == '':
                salary = 0
                proj = 0
                if sum(in_lineup) != 0:
                    in_lineup.fill(0)
                lineup = []
                hitter_teams = []
                pitcher_opps = []
                hitters_opposing_pitcher = 0
                k=0
                for pos in pos_matrix.T:
                    if k <num_p_on_roster:
                    # check for players eligible for the position and make sure they arent in a lineup, returns a list of indices of available player
                        valid_players = np.where((pos > 0) & (in_lineup == 0))
                        # grab names of players eligible
                        plyr_list = ids[valid_players]
                        # create np array of probability of being seelcted based on ownership and who is eligible at the position
                        prob_list = ownership[valid_players]
                        prob_list = prob_list / prob_list.sum()
                        choice = np.random.choice(a=plyr_list, p=prob_list)
                        choice_idx = np.where(ids == choice)[0]
                        lineup.append(str(choice))
                        in_lineup[choice_idx] = 1
                        salary += salaries[choice_idx]
                        proj += projections[choice_idx]
                        pitcher_opps.append(opponents[choice_idx][0])
                    if k >=num_p_on_roster:
                        p1_opp = pitcher_opps[0]
                        if num_p_on_roster == 2:
                            p2_opp = pitcher_opps[1]
                        else:
                            p2_opp = 'NOT_APPLICABLE'
                        if hitters_opposing_pitcher < overlap_limit:
                            valid_players = np.where((pos > 0) & (in_lineup == 0))
                            # grab names of players eligible
                            plyr_list = ids[valid_players]
                            # create np array of probability of being seelcted based on ownership and who is eligible at the position
                            prob_list = ownership[valid_players]
                            prob_list = prob_list / prob_list.sum()
                            choice = np.random.choice(a=plyr_list, p=prob_list)
                            choice_idx = np.where(ids == choice)[0]
                            lineup.append(str(choice))
                            in_lineup[choice_idx] = 1
                            salary += salaries[choice_idx]
                            proj += projections[choice_idx]
                            hitter_teams.append(teams[choice_idx][0])
                            if teams[choice_idx][0] == p1_opp:
                                hitters_opposing_pitcher += 1
                            if teams[choice_idx][0] == p2_opp:
                                hitters_opposing_pitcher += 1
                        else:
                            valid_players = np.where((pos > 0) & (in_lineup == 0)& (teams!=p1_opp)& (teams!=p2_opp))   
                            plyr_list = ids[valid_players]
                            # create np array of probability of being seelcted based on ownership and who is eligible at the position
                            prob_list = ownership[valid_players]
                            prob_list = prob_list / prob_list.sum()
                            choice = np.random.choice(a=plyr_list, p=prob_list)
                            choice_idx = np.where(ids == choice)[0]
                            lineup.append(str(choice))
                            in_lineup[choice_idx] = 1
                            salary += salaries[choice_idx]
                            proj += projections[choice_idx]
                            hitter_teams.append(teams[choice_idx][0]) 
                            if teams[choice_idx][0] == p1_opp:
                                hitters_opposing_pitcher += 1
                            if teams[choice_idx][0] == p2_opp:
                                hitters_opposing_pitcher += 1      
                    k +=1 
                # Must have a reasonable salary
                if salary >= salary_floor and salary <= salary_ceiling:
                    # Must have a reasonable projection (within 60% of optimal) **people make a lot of bad lineups
                    reasonable_projection = optimal_score - (
                        max_pct_off_optimal * optimal_score
                    )
                    if proj >= reasonable_projection:
                        mode = statistics.mode(hitter_teams)
                        if hitter_teams.count(mode) <= 5:                 
                            reject = False
                            lus[lu_num] = {
                                "Lineup": lineup,
                                "Wins": 0,
                                "Top10": 0,
                                "ROI": 0,
                                "Cashes": 0,
                                "Type": "generated_nostack",
                            }
            else:
                salary = 0
                proj = 0
                if sum(in_lineup) != 0:
                    in_lineup.fill(0)
                hitter_teams = []
                pitcher_opps = []
                filled_pos = np.zeros(shape=pos_matrix.shape[1])
                team_stack_len = 0
                k=0
                stack = True
                valid_team = np.where(teams == team_stack)[0]
                valid_players = np.unique(valid_team[np.where(pos_matrix[valid_team,2:]>0)[0]])
                hitters_opposing_pitcher = 0
                plyr_list = ids[valid_players]
                prob_list = ownership[valid_players]
                prob_list = prob_list / prob_list.sum()
                while stack: 
                    choices = np.random.choice(a=plyr_list, p=prob_list, size=stack_len, replace=False)
                    lineup = np.zeros(shape=pos_matrix.shape[1]).astype(str)
                    plyr_stack_indices = np.where(np.in1d(ids, choices))[0]
                    x=0
                    for p in plyr_stack_indices:
                        if '0.0' in lineup[np.where(p>0)[0]]:
                            for l in np.where(pos_matrix[p]>0)[0]:
                                if lineup[l] == '0.0':
                                    lineup[l] = ids[p]
                                    x+=1
                                    break
                    if x==stack_len:
                        in_lineup[plyr_stack_indices] =1
                        salary += sum(salaries[plyr_stack_indices])
                        #rint(salary)
                        proj += sum(projections[plyr_stack_indices])
                        #print(proj)
                        team_stack_len += stack_len
                        x=0
                        stack = False
                for ix, (l,pos) in enumerate(zip(lineup,pos_matrix.T)):
                    # get pitchers irrespective of stack
#                    print(lu_num,ix, l, pos, k, lineup)
                    if l == '0.0':
                        if k <num_p_on_roster:
                            valid_players = np.where((pos > 0) & (in_lineup == 0) & (opponents!=team_stack))
                            # grab names of players eligible
                            plyr_list = ids[valid_players]
                            # create np array of probability of being selected based on ownership and who is eligible at the position
                            prob_list = ownership[valid_players]
                            prob_list = prob_list / prob_list.sum()
                            #try:
                            choice = np.random.choice(a=plyr_list, p=prob_list)
                            #except:
                            #    print(k, pos)
                            choice_idx = np.where(ids == choice)[0]
                            in_lineup[choice_idx] = 1
                            lineup[ix] = str(choice)
                            salary += salaries[choice_idx]
                            proj += projections[choice_idx]
                            pitcher_opps.append(opponents[choice_idx][0])
                            k +=1                         
                        elif k >=num_p_on_roster:
                            p1_opp = pitcher_opps[0]
                            if num_p_on_roster == 2:
                                p2_opp = pitcher_opps[1]
                            else:
                                p2_opp = 'NOT_APPLICABLE'
                            if hitters_opposing_pitcher < overlap_limit:
                                valid_players = np.where((pos > 0) & (in_lineup == 0)& (teams!=team_stack))
                                # grab names of players eligible
                                plyr_list = ids[valid_players]
                                # create np array of probability of being seelcted based on ownership and who is eligible at the position
                                prob_list = ownership[valid_players]
                                prob_list = prob_list / prob_list.sum()
                                choice = np.random.choice(a=plyr_list, p=prob_list)
                                choice_idx = np.where(ids == choice)[0]
                                lineup[ix] = str(choice)
                                in_lineup[choice_idx] = 1
                                salary += salaries[choice_idx]
                                proj += projections[choice_idx]
                                hitter_teams.append(teams[choice_idx][0])
                                if teams[choice_idx][0] == p1_opp:
                                    hitters_opposing_pitcher += 1
                                if teams[choice_idx][0] == p2_opp:
                                    hitters_opposing_pitcher += 1
                                if teams[choice_idx][0] == team_stack:
                                    team_stack_len += 1
                            else:
                                valid_players = np.where((pos > 0) & (in_lineup == 0)& (teams!=p1_opp)& (teams!=p2_opp)& (teams!=team_stack))   
                                plyr_list = ids[valid_players]
                                # create np array of probability of being seelcted based on ownership and who is eligible at the position
                                prob_list = ownership[valid_players]
                                prob_list = prob_list / prob_list.sum()
                                choice = np.random.choice(a=plyr_list, p=prob_list)
                                choice_idx = np.where(ids == choice)[0]
                                lineup[ix] = str(choice)
                                in_lineup[choice_idx] = 1
                                salary += salaries[choice_idx]
                                proj += projections[choice_idx]
                                hitter_teams.append(teams[choice_idx][0]) 
                                if teams[choice_idx][0] == p1_opp:
                                    hitters_opposing_pitcher += 1
                                if teams[choice_idx][0] == p2_opp:
                                    hitters_opposing_pitcher += 1  
                                if teams[choice_idx][0] == team_stack:
                                    team_stack_len += 1                                    
                            k +=1 
                    else:
                        k+=1
                # Must have a reasonable salary
                if team_stack_len >=stack_len:
                    if salary >= salary_floor and salary <= salary_ceiling:
                    # loosening reasonable projection constraint for team stacks
                        reasonable_projection = optimal_score - (
                            (max_pct_off_optimal*1.25) * optimal_score
                        )
                        if proj >= reasonable_projection:
                            mode = statistics.mode(hitter_teams)
                            if hitter_teams.count(mode) <= 5:                 
                                reject = False
                                lus[lu_num] = {
                                    "Lineup": lineup,
                                    "Wins": 0,
                                    "Top10": 0,
                                    "ROI": 0,
                                    "Cashes": 0,
                                    "Type": "generated_stack",
                                }                
        return lus

    def generate_field_lineups(self):
        diff = self.field_size - len(self.field_lineups)
        if diff <= 0:
            print(
                "supplied lineups >= contest field size. only retrieving the first "
                + str(self.field_size)
                + " lineups"
            )
        else:
            print('Generating ' + str(diff) + ' lineups.')
            ids = []
            ownership = []
            salaries = []
            projections = []
            positions = []
            teams = []
            opponents = []
            for k in self.player_dict.keys():
                if 'Team' not in self.player_dict[k].keys():
                    print(self.player_dict[k]['Name'], ' name mismatch between projections and player ids!')
                ids.append(self.player_dict[k]['ID'])
                ownership.append(self.player_dict[k]['Ownership'])
                salaries.append(self.player_dict[k]['Salary'])
                projections.append(self.player_dict[k]['Fpts'])
                teams.append(self.player_dict[k]['Team'])
                opponents.append(self.player_dict[k]['Opp'])
                pos_list = []
                for pos in self.roster_construction:
                    if pos in self.player_dict[k]['Position']:
                        pos_list.append(1)
                    else:
                        pos_list.append(0)
                positions.append(np.array(pos_list))
            in_lineup = np.zeros(shape=len(ids))
            ownership = np.array(ownership)
            salaries = np.array(salaries)
            projections = np.array(projections)
            pos_matrix = np.array(positions)
            ids = np.array(ids)
            optimal_score = self.optimal_score
            salary_floor = self.min_lineup_salary
            salary_ceiling = self.salary
            max_pct_off_optimal = self.max_pct_off_optimal
            stack_usage = self.pct_field_using_stacks
            teams = np.array(teams)
            opponents = np.array(opponents)
            overlap_limit = self.overlap_limit
            problems = []
            stacks = np.random.binomial(n=1,p=self.pct_field_using_stacks,size=diff)
            stack_len = np.random.choice(a=[4,5],p=[1-self.pct_5man_stacks, self.pct_5man_stacks],size=diff)
            a = list(self.stacks_dict.keys())
            p = np.array(list(self.stacks_dict.values()))
            probs = p/sum(p)
            stacks = stacks.astype(str)
            for i in range(len(stacks)):
                if stacks[i] == '1':
                    choice = random.choices(a,weights=probs,k=1)
                    stacks[i] = choice[0]
                else:
                    stacks[i] = ''
            # creating tuples of the above np arrays plus which lineup number we are going to create
            #q = 0
            #for k in self.player_dict.keys():
                #if self.player_dict[k]['Team'] == stacks[0]:
                #    print(k, self.player_dict[k]['ID'])
                #    print(positions[q])
                #q += 1
            for i in range(diff):
                lu_tuple = (i, ids, in_lineup, pos_matrix,ownership, salary_floor, salary_ceiling, optimal_score, salaries, projections,max_pct_off_optimal, teams, opponents, stacks[i], stack_len[i], overlap_limit)
                problems.append(lu_tuple)
            #print(problems[0])
            #print(stacks)
            start_time = time.time()
            with mp.Pool() as pool:
                output = pool.starmap(self.generate_lineups, problems)
                print(
                    "number of running processes =",
                    pool.__dict__["_processes"]
                    if (pool.__dict__["_state"]).upper() == "RUN"
                    else None,
                )
                pool.close()
                pool.join()
            if len(self.field_lineups) == 0:
                new_keys = list(range(0, self.field_size))
            else:
                new_keys = list(
                    range(max(self.field_lineups.keys()) + 1, self.field_size)
                )
            nk = new_keys[0]
            for i, o in enumerate(output):
                if nk in self.field_lineups.keys():
                    print("bad lineups dict, please check dk_data files")
                self.field_lineups[nk] = o[i]
                nk += 1
            end_time = time.time()
            print("lineups took " + str(end_time - start_time) + " seconds")
            print(str(diff) + " field lineups successfully generated")
            #print(self.field_lineups)


    def calc_gamma(self,mean,sd):
        alpha = (mean / sd) ** 2
        beta = sd ** 2 / mean
        return alpha,beta




    def run_tournament_simulation(self):
        print("Running " + str(self.num_iterations) + " simulations")
        start_time = time.time()
        temp_fpts_dict = {}
        
        correlation_matrix = {
            1: {1: 1, 2: 0.2, 3: 0.175, 4: 0.15, 5: 0.125, 6: 0.1, 7: 0.075, 8: 0.05, 9: 0.025},
            2: {1: 0.2, 2: 1, 3: 0.2, 4: 0.175, 5: 0.15, 6: 0.125, 7: 0.1, 8: 0.075, 9: 0.05},
            3: {1: 0.175, 2: 0.2, 3: 1, 4: 0.2, 5: 0.175, 6: 0.15, 7: 0.125, 8: 0.1, 9: 0.075},
            4: {1: 0.15, 2: 0.175, 3: 0.2, 4: 1, 5: 0.2, 6: 0.175, 7: 0.15, 8: 0.125, 9: 0.1},
            5: {1: 0.125, 2: 0.15, 3: 0.175, 4: 0.2, 5: 1, 6: 0.2, 7: 0.175, 8: 0.15, 9: 0.125},
            6: {1: 0.1, 2: 0.125, 3: 0.15, 4: 0.175, 5: 0.2, 6: 1, 7: 0.2, 8: 0.175, 9: 0.15},
            7: {1: 0.075, 2: 0.1, 3: 0.125, 4: 0.15, 5: 0.175, 6: 0.2, 7: 1, 8: 0.2, 9: 0.175},
            8: {1: 0.05, 2: 0.075, 3: 0.1, 4: 0.125, 5: 0.15, 6: 0.175, 7: 0.2, 8: 1, 9: 0.2},
            9: {1: 0.025, 2: 0.05, 3: 0.075, 4: 0.1, 5: 0.125, 6: 0.15, 7: 0.175, 8: 0.2, 9: 1},
        }

        

        for p, s in self.player_dict.items():
            correlation_adjustment = 1.0  # No adjustment by default

            s["Adjusted Fpts"] = np.zeros(self.num_iterations)  # Initialize with zeros
            temp_fpts_dict[s["ID"]] = np.zeros(self.num_iterations)  # Same for temp_fpts_dict

        for i in range(self.num_iterations):  # Main iteration

            for p, s in self.player_dict.items():
                if "P" in s["Position"]:  # Pitcher
                    s["Adjusted Fpts"][i] = np.random.normal(
                        s["Fpts"], 
                        s["StdDev"] * self.randomness_amount / 100,
                    )

            for p, s in self.player_dict.items():
                if "P" not in s["Position"]:  # Batters
                    correlation_adjustment = 1.0
                    

                    # Teammate adjustment
                    teammates = [player for player, info in self.player_dict.items() if info["Team"] == s["Team"] and "P" not in info["Position"] and player != p]
                    if teammates:
                        correlations = [correlation_matrix[s["Order"]][self.player_dict[teammate]["Order"]] for teammate in teammates]
                        avg_correlation = sum(correlations) / len(correlations)  # Average of all correlations
                        correlation_adjustment += avg_correlation  


                    # Opposing pitcher adjustment
                    opposing_pitcher = [player for player, info in self.player_dict.items() if info["Team"] == s["Opp"] and "P" in info["Position"]]
                    if opposing_pitcher:
                        opposing_pitcher_id = [player for player, info in self.player_dict.items() if info["Team"] == s["Opp"] and "P" in info["Position"]][0]
                        opposing_pitcher_fpts = self.player_dict[opposing_pitcher_id]["Adjusted Fpts"][i]
                        

                        # no idea what this value should actually be re: 0.6

                        correlation_adjustment -= 0.6 * opposing_pitcher_fpts / s["Fpts"] 

                    a, b = self.calc_gamma(max(.1, s["Fpts"] * correlation_adjustment), s["StdDev"])
                    s["Adjusted Fpts"][i] = np.random.gamma(a, b)

                else:  # Pitchers, adjustment
                    opposing_hitters = [player for player, info in self.player_dict.items() if info["Team"] == s["Opp"] and "P" not in info["Position"]]
                    if opposing_hitters:
                        opposing_hitter_fpts = [self.player_dict[hitter]["Adjusted Fpts"][i] for hitter in opposing_hitters]  
                        correlation_adjustment -= 0.1 * sum(opposing_hitter_fpts) / s["Fpts"]  
                    temp_fpts_dict[s["ID"]][i] = s["Adjusted Fpts"][i] * correlation_adjustment  

        # generate arrays for every sim result for each player in the lineup and sum
        fpts_array = np.zeros(shape=(self.field_size, self.num_iterations))
        # converting payout structure into an np friendly format, could probably just do this in the load contest function
        payout_array = np.array(list(self.payout_structure.values()))
        # subtract entry fee
        payout_array = payout_array - self.entry_fee
        l_array = np.full(
            shape=self.field_size - len(payout_array), fill_value=-self.entry_fee
        )
        payout_array = np.concatenate((payout_array, l_array))

        for index, values in self.field_lineups.items():
            fpts_sim = sum([temp_fpts_dict[player] for player in values["Lineup"]])
            # store lineup fpts sum in 2d np array where index (row) corresponds to index of field_lineups and columns are the fpts from each sim
            fpts_array[index] = fpts_sim
        ranks = np.argsort(fpts_array, axis=0)[::-1]
        # count wins, top 10s vectorized
        wins, win_counts = np.unique(ranks[0, :], return_counts=True)
        t10, t10_counts = np.unique(ranks[0:9:], return_counts=True)
        roi = payout_array[np.argsort(ranks, axis=0)].sum(axis=1)
        # summing up ach lineup, probably a way to v)ectorize this too (maybe just turning the field dict into an array too)
        for idx in self.field_lineups.keys():
            # Winning
            if idx in wins:
                self.field_lineups[idx]["Wins"] += win_counts[np.where(wins == idx)][0]
            # Top 10
            if idx in t10:
                self.field_lineups[idx]["Top10"] += t10_counts[np.where(t10 == idx)][0]
            # can't figure out how to get roi for each lineup index without iterating and iterating is slow
            if self.use_contest_data:
                #    self.field_lineups[idx]['ROI'] -= (loss_counts[np.where(losses==idx)][0])*self.entry_fee
                self.field_lineups[idx]["ROI"] += roi[idx]
        end_time = time.time()
        diff = end_time - start_time
        print(
            str(self.num_iterations)
            + " tournament simulations finished in "
            + str(diff)
            + "seconds. Outputting."
        )

        # for p in self.player_dict.keys():
        #    print(p, self.player_dict[p]['ID'])

    def output(self):
        unique = {}
        for index, x in self.field_lineups.items():
            #print(x)
            salary = 0
            fpts_p = 0
            ceil_p = 0
            own_p = []
            lu_names = []
            lu_teams = []
            hitters_vs_pitcher = 0
            pitcher_opps = []
            for id in x["Lineup"]:
                for k,v in self.player_dict.items():
                    if v["ID"] == id:  
                        if 'P' in v["Position"]:
                            pitcher_opps.append(v['Opp'])         
            for id in x["Lineup"]:
                for k,v in self.player_dict.items():
                    if v["ID"] == id:
                        salary += v["Salary"]
                        fpts_p += v["Fpts"]
                        ceil_p += v["Ceiling"]
                        own_p.append(v["Ownership"]/100)
                        lu_names.append(v["Name"])
                        if 'P' not in v["Position"]:
                            lu_teams.append(v['Team'])
                            if v['Team'] in pitcher_opps:
                                hitters_vs_pitcher += 1
                        continue
            counter = collections.Counter(lu_teams)
            stacks = counter.most_common(2)
            own_p = np.prod(own_p)
            win_p = round(x["Wins"] / self.num_iterations * 100, 2)
            top10_p = round(x["Top10"] / self.num_iterations * 100, 2)
            cash_p = round(x["Cashes"] / self.num_iterations * 100, 2)
            lu_type = x["Type"]
            if self.site == "dk":
                if self.use_contest_data:
                    roi_p = round(
                        x["ROI"] / self.entry_fee / self.num_iterations * 100, 2
                    )
                    roi_round = round(x["ROI"] / self.num_iterations, 2)
                    lineup_str = "{} ({}),{} ({}),{} ({}),{} ({}),{} ({}),{} ({}),{} ({}),{} ({}),{} ({}),{} ({}),{},{},${},{}%,{}%,{}%,{},${},{},{},{},{}".format(
                        lu_names[0].replace("#", "-"),
                        x["Lineup"][0],
                        lu_names[1].replace("#", "-"),
                        x["Lineup"][1],
                        lu_names[2].replace("#", "-"),
                        x["Lineup"][2],
                        lu_names[3].replace("#", "-"),
                        x["Lineup"][3],
                        lu_names[4].replace("#", "-"),
                        x["Lineup"][4],
                        lu_names[5].replace("#", "-"),
                        x["Lineup"][5],
                        lu_names[6].replace("#", "-"),
                        x["Lineup"][6],
                        lu_names[7].replace("#", "-"),
                        x["Lineup"][7],
                        lu_names[8].replace("#", "-"),
                        x["Lineup"][8],
                        lu_names[9].replace("#", "-"),
                        x["Lineup"][9],
                        fpts_p,
                        ceil_p,
                        salary,
                        win_p,
                        top10_p,
                        roi_p,
                        own_p,
                        roi_round,
                        str(stacks[0][0]) + ' ' + str(stacks[0][1]),
                        str(stacks[1][0]) + ' ' + str(stacks[1][1]),
                        hitters_vs_pitcher,
                        lu_type
                    )
                else:
                    lineup_str = "{} ({}),{} ({}),{} ({}),{} ({}),{} ({}),{} ({}),{} ({}),{} ({}),{} ({}),{},{}%,{}%,{},{}%,{}".format(
                        lu_names[0].replace("#", "-"),
                        x["Lineup"][0],
                        lu_names[1].replace("#", "-"),
                        x["Lineup"][1],
                        lu_names[2].replace("#", "-"),
                        x["Lineup"][2],
                        lu_names[3].replace("#", "-"),
                        x["Lineup"][3],
                        lu_names[4].replace("#", "-"),
                        x["Lineup"][4],
                        lu_names[5].replace("#", "-"),
                        x["Lineup"][5],
                        lu_names[6].replace("#", "-"),
                        x["Lineup"][6],
                        lu_names[7].replace("#", "-"),
                        x["Lineup"][7],
                        lu_names[8].replace("#", "-"),
                        x["Lineup"][8],
                        lu_names[9].replace("#", "-"),
                        x["Lineup"][9],
                        fpts_p,
                        ceil_p,
                        salary,
                        win_p,
                        top10_p,
                        own_p,
                        cash_p,
                        lu_type,
                    )
            elif self.site == "fd":
                if self.use_contest_data:
                    roi_p = round(
                        x["ROI"] / self.entry_fee / self.num_iterations * 100, 2
                    )
                    roi_round = round(x["ROI"] / self.num_iterations, 2)
                    lineup_str = "{}:{},{}:{},{}:{},{}:{},{}:{},{}:{},{}:{},{}:{},{}:{},{},{},{},{}%,{}%,{}%,{},${},{}".format(
                        self.id_name_dict[x["Lineup"][0].replace("-", "#")]["ID"],
                        lu_names[0].replace("#", "-"),
                        self.id_name_dict[x["Lineup"][1].replace("-", "#")]["ID"],
                        lu_names[1].replace("#", "-"),
                        self.id_name_dict[x["Lineup"][2].replace("-", "#")]["ID"],
                        lu_names[2].replace("#", "-"),
                        x["Lineup"][3],
                        lu_names[3].replace("#", "-"),
                        x["Lineup"][4],
                        lu_names[4].replace("#", "-"),
                        x["Lineup"][5],
                        lu_names[5].replace("#", "-"),
                        x["Lineup"][6],
                        lu_names[6].replace("#", "-"),
                        x["Lineup"][7],
                        lu_names[7].replace("#", "-"),
                        x["Lineup"][8],
                        lu_names[8].replace("#", "-"),
                        fpts_p,
                        ceil_p,
                        salary,
                        win_p,
                        top10_p,
                        roi_p,
                        own_p,
                        roi_round,
                        lu_type
                    )
                else:
                    lineup_str = "{}:{},{}:{},{}:{},{}:{},{}:{},{}:{},{}:{},{}:{},{}:{},{},{},{},{}%,{}%,{},{}%,${},{}".format(
                        x["Lineup"][0],
                        lu_names[0].replace("#", "-"),
                        x["Lineup"][1],
                        lu_names[1].replace("#", "-"),
                        x["Lineup"][2],
                        lu_names[2].replace("#", "-"),
                        x["Lineup"][3],
                        lu_names[3].replace("#", "-"),
                        x["Lineup"][4],
                        lu_names[4].replace("#", "-"),
                        x["Lineup"][5],
                        lu_names[5].replace("#", "-"),
                        x["Lineup"][6],
                        lu_names[6].replace("#", "-"),
                        x["Lineup"][7],
                        lu_names[7].replace("#", "-"),
                        x["Lineup"][8],
                        lu_names[8].replace("#", "-"),
                        fpts_p,
                        ceil_p,
                        salary,
                        win_p,
                        top10_p,
                        own_p,
                        cash_p,
                        lu_type
                    )
            unique[index] = lineup_str

        out_path = os.path.join(
            os.path.dirname(__file__),
            "../output/{}_gpp_sim_lineups_{}_{}.csv".format(
                self.site, self.field_size, self.num_iterations
            ),
        )
        with open(out_path, "w") as f:
            if self.site == "dk":
                if self.use_contest_data:
                    f.write(
                        "P,P,C,1B,2B,3B,SS,OF,OF,OF,Fpts Proj,Ceiling,Salary,Win %,Top 10%,ROI%,Proj. Own. Product, Avg. Return,Stack1 Type, Stack2 Type, Num Opp Hitters, Lineup Type\n"
                    )
                else:
                    f.write(
                        "P,P,C,1B,2B,3B,SS,OF,OF,OF,Fpts Proj,Ceiling,Salary,Win %,Top 10%,Proj. Own. Product,Cash %,Type\n"
                    )
            elif self.site == "fd":
                if self.use_contest_data:
                    f.write(
                        "P,C/1B,2B,3B,SS,OF,OF,OF,UTIL,Fpts Proj,Ceiling,Salary,Win %,Top 10%,ROI%,Proj. Own. Product, Avg. Return,Type\n"
                    )
                else:
                    f.write(
                        "P,C/1B,2B,3B,SS,OF,OF,OF,UTIL,Fpts Proj,Ceiling,Salary,Win %,Top 10%,Proj. Own. Product,Cash %,Type\n"
                    )

            for fpts, lineup_str in unique.items():
                f.write("%s\n" % lineup_str)

        out_path = os.path.join(
            os.path.dirname(__file__),
            "../output/{}_gpp_sim_player_exposure_{}_{}.csv".format(
                self.site, self.field_size, self.num_iterations
            ),
        )
        with open(out_path, "w") as f:
            f.write("Player,Win%,Top10%,Sim. Own%,Proj. Own%,Avg. Return\n")
            unique_players = {}
            for val in self.field_lineups.values():
                for player in val["Lineup"]:
                    if player not in unique_players:
                        unique_players[player] = {
                            "Wins": val["Wins"],
                            "Top10": val["Top10"],
                            "In": 1,
                            "ROI": val["ROI"],
                        }
                    else:
                        unique_players[player]["Wins"] = (
                            unique_players[player]["Wins"] + val["Wins"]
                        )
                        unique_players[player]["Top10"] = (
                            unique_players[player]["Top10"] + val["Top10"]
                        )
                        unique_players[player]["In"] = unique_players[player]["In"] + 1
                        unique_players[player]["ROI"] = (
                            unique_players[player]["ROI"] + val["ROI"]
                        )

            for player, data in unique_players.items():
                field_p = round(data["In"] / self.field_size * 100, 2)
                win_p = round(data["Wins"] / self.num_iterations * 100, 2)
                top10_p = round(data["Top10"] / self.num_iterations / 10 * 100, 2)
                roi_p = round(data["ROI"] / data["In"] / self.num_iterations, 2)
                for k,v in self.player_dict.items():
                    if player == v["ID"]:
                        proj_own = v["Ownership"]
                        p_name = v["Name"]
                        break
                f.write(
                    "{},{}%,{}%,{}%,{}%,${}\n".format(
                        p_name.replace("#","-"),
                        win_p,
                        top10_p,
                        field_p,
                        proj_own,
                        roi_p,
                    )
                )
