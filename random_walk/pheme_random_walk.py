"""
* All edges are undirected.
* All IDs are string.
"""
import random
from tqdm import tqdm
import os
from multiprocessing import Manager, Pool

# overall
num_process = 16

# input
in_dir = '/rwproject/kdd-db/20-rayw1/pheme-figshare'

node_types = ['n', 'p', 'u']
edge_files = {
    ('n', 'p'): 'PhemeNewsPost.txt',
    ('n', 'u'): 'PhemeNewsUser.txt',
    ('p', 'p'): 'PhemePostPost.txt',
    ('p', 'u'): 'PhemePostUser.txt',
    ('u', 'u'): 'PhemeUserUser.txt',
}
edges_to_enforce = {  # must be in the output neighbor file
    ('n', 'u'),
}

# for random walk with restart
restart_rate = 0.5
min_neigh = {
    'n': 50,
    'p': 50,
    'u': 900,
}
num_neigh_to_record = 1300
max_steps = 10000

# for neighbor selection
max_uniq_neigh = {
    'n': 5,
    'p': 5,
    'u': 100,
}

# output
configuration_tag = f'pheme_' + '_'.join([f'{k}{max_uniq_neigh[k]}' for k in node_types])
output_dir = f"rwr_results/{configuration_tag}"

# global
adj_list = dict()  # IN  adj_list['s123'] = ['u456', 'n789', ...]

def recompute_involved(nei_list):
    involved = {t : set() for t in node_types}
    for node_1, nei_d in tqdm(nei_list.items(), desc='recompute involved'):
        involved[node_1[0]].add(node_1)
        for nodes in nei_d.values():
            for node_2 in nodes:
                involved[node_2[0]].add(node_2)
    return involved

def rwr_worker(start_node, nei_list_subsets, desc, j, nodes_len):
    nei_list = {start_node : {t : [] for t in node_types}}  # OUT nei_list['p123']['u'] = ['u456', 'u789', ...]

    def try_add_neighbor(start_node, cur_node, num_neighs):
        t = cur_node[0]
        if len(nei_list[start_node][t]) < min_neigh[t] or \
                all([len(nei_list[start_node][s]) >= min_neigh[s] for s in node_types if s != t]):
            nei_list[start_node][t].append(cur_node)
            return num_neighs + 1
        else:
            return num_neighs

    def get_top_k_most_frequent(neighbors, k, exclude):
        counter = dict()
        for node in neighbors:
            if node not in counter:
                counter[node] = 0
            counter[node] += 1
        counter.pop(exclude, None)
        items = sorted(list(counter.items()), key=lambda x: -x[1])
        neighbors[:] = [items[i][0] for i in range(min(k, len(items)))]

    def enforce_edges(top_k, node, nn, k):
        for neig in adj_list[node]:
            if neig[0] == nn and neig not in top_k:
                top_k.insert(0, neig)
        top_k[:] = top_k[:k]

    def write_neighbor(node):
        for nn in node_types:
            get_top_k_most_frequent(nei_list[node][nn], max_uniq_neigh[nn], exclude=node)
            if (node[0], nn) in edges_to_enforce or (nn, node[0]) in edges_to_enforce:
                enforce_edges(nei_list[node][nn], node, nn, max_uniq_neigh[nn])
    
    cur_node = start_node
    num_neighs, steps = 0, 0
    while num_neighs < num_neigh_to_record and steps < max_steps:
        rand_p = random.random()  # return p
        if rand_p < restart_rate:
            cur_node = start_node
        else:
            cur_node = random.choice(adj_list[cur_node])
            num_neighs = try_add_neighbor(start_node, cur_node, num_neighs)
        steps += 1
    write_neighbor(start_node)
    
    nei_list_subsets.append(nei_list)
    print(desc, '{:7} {:7} {:.4}'.format(j, nodes_len, j/nodes_len))

def save_result_worker(nei_list, involved, t, return_dict):
    written = 0
    with open(os.path.join(output_dir, f'{t}_neighbors.txt'), 'w') as f:
        for node, type_neighs in tqdm(nei_list.items(), desc=f'write {t} neigh'):
            if node[0] == t:
                if all([len(type_neighs[t]) == 0 for t in node_types]):
                    continue
                f.write(node + ':')
                for neig_type in node_types:
                    f.write(' ' + ' '.join(type_neighs[neig_type]))
                    f.write((' ' + neig_type + 'PADDING') * (
                        max(0, max_uniq_neigh[neig_type] - len(type_neighs[neig_type]))
                    ))
                    written += len(type_neighs[neig_type])
                f.write('\n')
    with open(os.path.join(output_dir, f'{t}_involved.txt'), "w") as f:
        f.write(' '.join(list(involved[t])) + "\n")
    ret_str = "type {}: {:10} neighbors written.\n".format(t, written) + \
              "        {:10} nodes involved.\n".format(len(involved[t]))
    return_dict[t] = ret_str

def random_walk_with_restart():
    nei_list, nodes = dict(), dict()
    nodes = {t : set() for t in node_types}     # IN  nodes['p'] = {'p123', 'p456', ...}
    involved = {t : set() for t in node_types}  # OUT involved['p'] = {'p123', 'p456', ...}
    manager = Manager()

    def add_adjacent(m, n):
        if m not in adj_list.keys():
            adj_list[m] = []
        adj_list[m].append(n)

    def update_nei_list_subsets_recusive(nei_list_subsets):
        def _update_nei_list_subsets_recusion(lidx, ridx):  # [lidx, ridx)
            if ridx - lidx == 0:
                return dict()
            if ridx - lidx == 1:
                return nei_list_subsets[lidx]
            midx = (lidx + ridx) // 2
            l = _update_nei_list_subsets_recusion(lidx, midx)
            r = _update_nei_list_subsets_recusion(midx, ridx)
            l.update(r)
            return l
        return _update_nei_list_subsets_recusion(0, len(nei_list_subsets))

    def rwr(nodes_set, desc):
        nodes_list = list(nodes_set)
        nei_list_subsets = manager.list()
        with Pool(num_process) as p:
            p.starmap(rwr_worker, [(nodes_list[i], nei_list_subsets, desc, i, len(nodes_list)) for i in range(len(nodes_list))])
        nei_list.update(update_nei_list_subsets_recusive(nei_list_subsets))
    
    def compute_stats():
        stats = {t1: {t2: [] for t2 in node_types} for t1 in node_types}
        for n1, v in tqdm(nei_list.items(), desc='compute_stats'):
            for t2, x in v.items():
                stats[n1[0]][t2].append(len(x))
        stats_str = []
        for t1 in node_types:
            for t2 in node_types:
                stats_str.append('stats {} {} {:.6f}'.format(
                    t1, t2,
                    sum(stats[t1][t2]) / len(stats[t1][t2]) if len(stats[t1][t2]) > 0 else 0))
        return stats_str
    
    def save_result():
        return_dict = manager.dict()
        with Pool(len(node_types)) as p:
            p.starmap(save_result_worker, [(nei_list, involved, t, return_dict) for t in node_types])
        return [_ for _ in return_dict.values()]

    print("Read the graph...")
    edge_dir = in_dir
    print("Reading", edge_dir)
    for (main_type, neig_type), edge_f in edge_files.items():
        with open(os.path.join(edge_dir, edge_f), "r") as f:
            for l in tqdm(f.readlines(), desc='read ' + main_type+' '+neig_type):  ########################################
                l = l.strip().split()
                add_adjacent(main_type + l[0], neig_type + l[1])
                add_adjacent(neig_type + l[1], main_type + l[0])
                nodes[main_type].add(main_type + l[0])
                nodes[neig_type].add(neig_type + l[1])

    print("Each node takes turns to be the starting node...")
    rwr(nodes['n'], 'news rwr')
    involved = recompute_involved(nei_list)
    rwr(involved['p'], 'post rwr')
    involved = recompute_involved(nei_list)
    rwr(involved['u'], 'user rwr')
    involved = recompute_involved(nei_list)

    print("Save the result...")
    strs = []
    strs.extend(save_result())
    strs.extend(compute_stats())
    for s in strs:
        print(s)
    with open(os.path.join(output_dir, f'stats.txt'), 'w') as f:
        f.write('\n'.join(strs) + '\n')


if __name__ == "__main__":
    if not os.path.isdir(output_dir):
        os.mkdir(output_dir)

    print("\n" + "- " * 10 + configuration_tag + " -" * 10 + "\n")
    print('Files output to', output_dir)
    random_walk_with_restart()
