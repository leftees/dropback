# -*- coding: utf-8 -*-
"""
    dropback.backup
    ~~~~~~~~~~~~~~

    The main application; backs up directory to dropbox

    Must be run as Python2, as Dropbox Library doesn't support Python3 yet

    :author: Jonathan Love
    :copyright: (c) 2015 by Doubledot Media Ltd.
    :license: See README.md and LICENSE for more details
"""
# Script must be run as Python2 as Dropbox SDK is not Python3 compatible yet

import dropbox
import argparse
import os
import time
import stat
import re
import json
import pickle
import copy
import pprint
import logging

from node import NFolder, NFile, NRootFolder

CONFIG_SEARCH_PATHS = [os.path.dirname(os.path.realpath(__file__)), "/etc/dropbox_backups.d", ".", "./conf", "/etc", "/root/scripts/backups"]

APP_KEY     = "YOUR_KEY_HERE"
APP_SECRET  = "YOUR_SECRET_HERE"
ACCESS_TYPE = "app_folder"


def get_active_config_path(config_file):
    for path in CONFIG_SEARCH_PATHS:
        if os.path.exists(path) and os.path.exists(os.path.join(path, config_file)) and os.path.isfile(os.path.join(path, config_file)):
            return os.path.join(path, config_file)
    return os.path.join(CONFIG_SEARCH_PATHS[0], config_file)


def parse_target(target):
    logging.debug("Parsing target: {}".format(target))
    # We don't validate the path, that's up to Dropbox; however we do check for a leading slash
    target_match = re.compile("([a-zA-Z_][a-zA-Z0-9_-]*?):(/.*)")
    matches = target_match.match(target)
    if not matches:
        raise Exception("Dropbox source/target isn't written correctly")
    return matches.group(1), matches.group(2)

def diff_trees_r(local, remote, max_recurse_depth=-1):
    logging.debug("diff_trees_r: Recurse depth {}".format(max_recurse_depth))
    diff_node = copy.copy(local)
    diff_node.children = []

    if diff_node.size == remote.size and diff_node.mtime == remote.mtime:
        # We've already been uploaded
        diff_node.uploaded = True

    # Diff this folder and check if any nodes are different
    l_nodes = sorted([f for f in local.children], key=lambda x: x.name)
    r_nodes = sorted([f for f in remote.children], key=lambda x: x.name)

    # Walk like a mergesort, kind of - compare nodes right and left
    l_pointer = 0
    r_pointer = 0
    
    while l_pointer < len(l_nodes) and r_pointer < len(r_nodes):
        # This can be necessary if the index was rebuilt. 
        # @TODO support UTF-8 correctly
        
        l_name = l_nodes[l_pointer].name.decode('utf-8')
        r_name = r_nodes[r_pointer].name.decode('utf-8')

        if l_name == r_name:
            # Object exists locally *and* remotely

            diff_child = l_nodes[l_pointer]
            # Right, now compare if node is the same type and has matching class type
            if l_nodes[l_pointer].size  == r_nodes[r_pointer].size and \
               l_nodes[l_pointer].mtime == r_nodes[r_pointer].mtime and \
               l_nodes[l_pointer].mode == r_nodes[r_pointer].mode and \
               l_nodes[l_pointer].gid == r_nodes[r_pointer].gid and \
               l_nodes[l_pointer].uid == r_nodes[r_pointer].uid:
                # Node is not different! Use already uploaded one
                diff_child = r_nodes[r_pointer]
            
            # Node is the same
            if isinstance(l_nodes[l_pointer], NFolder) and isinstance(r_nodes[r_pointer], NFolder):
                if max_recurse_depth != 0:
                    diff_child = diff_trees_r(l_nodes[l_pointer], r_nodes[r_pointer], max_recurse_depth-1)
            elif isinstance(l_nodes[l_pointer], NFolder) or isinstance(r_nodes[r_pointer], NFolder):
                # Uh oh, but one of these is
                logging.warning("One of l_nodes[l_pointer] ({}) or r_nodes[r_pointer] ({}) is not an NFolder, but one is".format(l_nodes[l_pointer], r_nodes[r_pointer]))

            if diff_child:
                diff_node.children.append(diff_child)

            # Move up the file pointers
            l_pointer = l_pointer + 1
            r_pointer = r_pointer + 1

            
        elif l_name < r_name:
            # File exists locally but not remotely. We must sync it!
            diff_node.children.append(l_nodes[l_pointer])
            l_pointer = l_pointer + 1

        elif l_name > r_name:
            # File exists remotely but no longer exists locally.
            # We have no deletion logic for now
            child = r_nodes[r_pointer]
            child.todelete = True
            diff_node.children.append(child)
            r_pointer = r_pointer + 1
        else:
            error_stats = {
                "msg": "Shouldn't have got here",
                "l_pointer": l_pointer,
                "r_pointer": r_pointer,
                "l_nodes": len(l_nodes),
                "r_nodes": len(r_nodes),
                "l_nodes.name": l_nodes[l_pointer].name,
                "r_nodes.name": r_nodes[r_pointer].name,
            }
            raise Exception(error_stats)


    # If there were any local nodes remaining, ensure they're processed
    # We can't mark remote children for deletion yet
    diff_node.children.extend(l_nodes[l_pointer:])

    # If there were any remote nodes remaining, ensure they're processed
    # We can't mark remote children for deletion yet
    right_children = r_nodes[r_pointer:]
    for child in right_children:
        child.todelete = True
    diff_node.children.extend(right_children)

    return diff_node if len(diff_node.children) > 0 else None


def diff_trees(local_root_node, remote_root_node, max_recurse_depth=-1):
    # Walk the trees
    # We always want to overwrite remote with the local root
    if max_recurse_depth != 0:
        diff_child = diff_trees_r(local_root_node, remote_root_node)
    
    if not diff_child:
        # We always need to return a node
        diff_child = diff_node = copy.copy(local_root_node)
        diff_child.children = []

    return diff_child


def backup(args, client):
    if not os.path.exists(args.source):
        raise Exception("Source directory does not exist")

    target, target_folder = parse_target(args.destination)
    try:
        # Attempt to create target's folder
        # Safer than checking *then* attempting to create
        client.file_create_folder("/{target}".format(target=target))
        client.file_create_folder("/{target}/data".format(target=target))
        base_path = "/{target}/data".format(target=target)
        for p in target_folder.split("/"):
            base_path = "/".join([base_path, p])
            client.file_create_folder(base_path)
    except dropbox.rest.ErrorResponse as e:
        # Ignore if already exists
        if e.status != 403:
            raise

    logging.info("Getting a list of already backed up remote files")
    remote_node_tree = NRootFolder()
    remote_node_tree.walk_remote_tree_r(client, target, target_folder)

    #pprint.pprint(remote_node_tree.encodable())

    # Generate the local metadata index
    logging.info("Getting a list of local files")
    local_node_tree = NRootFolder()
    local_node_tree.walk_local_tree_r(args.source)

    logging.info("Generating list of which specific files to backup")
    nodes_to_upload = diff_trees(local_node_tree, remote_node_tree)
    
    # Now do the upload
    nodes_to_upload.upload(args.source, client, target, target_folder, overwrite_mode = True)



def restore(args, client):
    if not os.path.exists(args.destination):
        raise Exception("Restore directory does not exist")

    source, source_folder = parse_target(args.source)

    nodes_to_restore = NRootFolder()
    nodes_to_restore.walk_remote_tree_r(client, source, source_folder)

    # @TODO: Some logic to prevent overwriting the local tree unless we want to...
    print "Restoring these files and folders:"
    pprint.pprint(nodes_to_restore.encodable())

    nodes_to_restore.restore(args.destination, client, source, source_folder, overwrite_mode=True)


def rebuild(args, client):
    target, target_folder = parse_target(args.target)

    remote_node_tree = NRootFolder()
    remote_node_tree.rewrite_index_without_assumption_tree_r(client, target, target_folder)


def connect():
    dropbox_sess = dropbox.session.DropboxSession(APP_KEY, APP_SECRET, ACCESS_TYPE)
    request_token = dropbox_sess.obtain_request_token()

    url = dropbox_sess.build_authorize_url(request_token)
    print
    print "URL: {url}".format(url=url)
    print "Please visit the above URL in your browser and click Allow"
    print 
    print "This script will continue once you've completed the above step"

    authenticated = False
    access_token = None

    time.sleep(5)
    while not authenticated:
        try:
            access_token = dropbox_sess.obtain_access_token(request_token)
            authenticated = True
        except:
            time.sleep(2)
    client = dropbox.client.DropboxClient(dropbox_sess)
    print ("-----------------------------------------------------------------------")
    print ("Linked to {fullname}'s account".format(fullname=client.account_info()['display_name']))
    _store_credentials(access_token)


def _clear_credentials():
    os.unlink(get_active_config_path("dropbox_backup_credentials"))

def _store_credentials(access_token):
    with open (get_active_config_path("dropbox_backup_credentials"), "w") as cred_file:
        pickle.dump(access_token, cred_file)

def _load_credentials():
    with open (get_active_config_path("dropbox_backup_credentials"), "r") as cred_file:
        access_token = pickle.load(cred_file)
    return access_token


def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description='Backup/Restore files to/from Dropbox')
    parser.add_argument('command', help="Command to run")
    args, remainder_args = parser.parse_known_args()
    command = args.command.lower().strip()
    if command == "disconnect":
        try:
            _load_credentials()
            _clear_credentials()
        except IOError:
            print "Warning: Dropbox credentials not found; nothing to disconnect."
            print "Nothing to do."
    elif command == "connect":
        try:
            access_token = _load_credentials()
            dropbox_sess = dropbox.session.DropboxSession(APP_KEY, APP_SECRET, ACCESS_TYPE)
            dropbox_sess.set_token(access_token.key, access_token.secret)
            client = dropbox.client.DropboxClient(dropbox_sess)
            print "Error: Already linked to {fullname}'s account.".format(fullname=client.account_info()['display_name'])
            print "Please run `<scriptname> disconnect` first."
            print "Nothing to do."
        except (IOError, dropbox.rest.ErrorResponse):
            connect()
    else:
        try:
            access_token = _load_credentials()
            dropbox_sess = dropbox.session.DropboxSession(APP_KEY, APP_SECRET, ACCESS_TYPE)
            dropbox_sess.set_token(access_token.key, access_token.secret)
            client = dropbox.client.DropboxClient(dropbox_sess)
            print "Linked to {fullname}'s account".format(fullname=client.account_info()['display_name'])
        except (IOError, dropbox.rest.ErrorResponse) as e:
            print "Error: Valid dropbox credentials not found; please run `<scriptname> connect` to connect to Dropbox"
            raise
        
        if command == "backup":
            logging.info("Preparing to backup files")
            parser = argparse.ArgumentParser(description='Backup files to Dropbox')
            parser.add_argument('command', help="Command to run")
            parser.add_argument('source', help="Source folder")
            parser.add_argument('destination', help="Dropbox target (In the form <backup-root-name>:/subfolder)")
            args = parser.parse_args([command].extend(remainder_args))
            backup(args, client)

        elif command == "restore":
            logging.info("Preparing to restore files")
            parser = argparse.ArgumentParser(description='Restore files from Dropbox. WARNING, will overwrite local copies')
            parser.add_argument('command', help="Command to run")
            parser.add_argument('source', help="Dropbox source (In the form <backup-root-name>:/subfolder)")
            parser.add_argument('destination', help="Destination folder")
            args = parser.parse_args([command].extend(remainder_args))
            restore(args, client)

        elif command == "rebuild":
            logging.info("Rebuilding the backup index")
            parser = argparse.ArgumentParser(description='Rebuild the backup index in Dropbox, in case the initial backup fails, files have been deleted from Dropbox, or the index is corrupted')
            parser.add_argument('command', help="Command to run")
            parser.add_argument('target', help="Target to rebuild (In the form <backup-root-name>:/subfolder)")
            args = parser.parse_args([command].extend(remainder_args))
            rebuild(args, client)

        else:
            raise Exception("Please specify a valid command")

    logging.info("Done")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()