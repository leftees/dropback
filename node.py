# -*- coding: utf-8 -*-
"""
    backup-to-dropbox.node
    ~~~~~~~~~~~~~~

    Classes for working with files and folders as 'nodes' in Dropbox

    Must be run as Python2, as Dropbox Library doesn't support Python3 yet

    :author: Jonathan Love
    :copyright: (c) 2015 by Doubledot Media Ltd.
    :license: See README.md and LICENSE for more details
"""
import logging
import os
import dropbox
import StringIO
import glob
import json
import traceback


class UnknownNodeTypeException(Exception):
    pass


class NFile(object):
    # 100 MB chunk limit. 
    # Dropbox actually states 120MB is the limit but this is lower to be safer
    CHUNKED_SIZE_LIMIT = 1024*1024*100

    name   = None
    uid    = None
    gid    = None
    mode   = None
    mtime  = None
    ctime  = None
    size   = None

    parent = None

    symlink_target = None

    uploaded = False
    todelete = False

    def __init__(self, parent, name, stats):
        self.name  = name
        self.parent = parent
        self.uid   = stats["uid"]
        self.gid   = stats["gid"]
        self.mode  = stats["mode"]
        self.mtime = stats["mtime"]
        self.ctime = stats["ctime"]
        self.size  = stats["size"]

    def encodable(self, max_recurse_depth=-1, only_uploaded=True):
        # We accept extra parameters on encodable that NFile doesn't need
        # because it's easier than extra logic on NFolder
        obj = {
            "_type": "NFile",
            "name": self.name,
            "uploaded": self.uploaded,
            "stats": {
                "uid": self.uid,
                "gid": self.gid,
                "mode": self.mode,
                "mtime": self.mtime,
                "ctime": self.ctime,
                "size": self.size
            }
        }
        if self.symlink_target:
            obj["symlink_target"] = self.symlink_target

        return obj

    # Replace this with a more efficient approach
    def generate_path (self):
        current_path = os.path.join(self.name)
        node = self.parent
        while node != None:
            current_path = os.path.join(node.name, current_path)
            node = node.parent
        return current_path

    def generate_full_path(self, source_base):
        return os.path.join(source_base, self.generate_path())

    def restore(self, local_base, dropbox_client, source, source_base="/", overwrite_mode=True, max_recurse_depth=-1):      
        path = self.generate_path()
        full_local_path = os.path.join(local_base, path)
        source_path = "/".join([source_base, path])
        full_remote_path = "/{source}/data{path}".format(source=source, path=source_path)

        try:
            if not os.path.exists(full_local_path) or overwrite_mode:
                if not self.symlink_target:

                    out = open(full_local_path, "wb")
                    with dropbox_client.get_file(full_remote_path) as f:
                        out.write(f.read())
                    out.close()

                    if self.mode:
                        os.chmod(full_local_path, self.mode)
                    
                    if self.uid and self.gid:
                        os.chown(full_local_path, self.uid, self.gid)

                    if self.mtime:
                        # Set atime to mtime as well
                        os.utime(full_local_path,(self.mtime, self.mtime))
                else:
                    os.symlink(self.symlink_target, full_local_path)
                    # Symlink stats don't actually matter
            else:
                logging.info("File {} already exists, not overwriting it".format(full_local_path))

        except Exception as e:
            logging.error("Could not restore NFile {remote_path}' to '{local_path}'".format(local_path=path, remote_path=full_remote_path))
            logging.error("{}".format(e))
            logging.error(traceback.format_exc())
            logging.error("Skipping NFile {remote_path}".format(remote_path=full_remote_path))

    def upload(self, source_base, dropbox_client, target, target_base="/", overwrite_mode=True, max_recurse_depth=-1):
        if not self.uploaded:
            path = self.generate_path()
            full_local_path = os.path.join(source_base, path)
            target_path = "/".join([target_base, path])
            full_remote_path = "/{target}/data{path}".format(target=target, path=target_path)

            try:
                # Let's start by confirming the remote file/folder
                logging.info("Backing up NFile '{local_path}' to '{remote_path}'".format(local_path=path, remote_path=full_remote_path))
                
                if self.symlink_target:
                    full_remote_path = "{name}.symlink".format(name=full_remote_path)

                meta = {}
                try:
                    meta = dropbox_client.metadata(full_remote_path)
                except (dropbox.rest.ErrorResponse) as e:
                    if e.status != 404:
                        raise e

                if not overwrite_mode and "is_dir" in meta and meta["is_dir"] is True:
                    # We care if the remote path is a dir and we're a file, UNLESS we don't want to overwrite anyway.
                    logging.warning("Folder exists at remote location; attempting removal...")
                    dropbox_client.file_delete(full_remote_path)

                if self.symlink_target:
                    # If we're a symlink, upload as symlink
                    response = dropbox_client.put_file(
                                    "{path}".format(path=full_remote_path),
                                    file_obj=self.symlink_target,
                                    overwrite=overwrite_mode
                                )
                else:
                    # Right, now to actually upload 
                    with open(full_local_path, "rb") as file_h:
                        # @TODO: Update the file size/stats incase they've changed since we enumerated the directories
                        if self.size < self.CHUNKED_SIZE_LIMIT:
                            response = dropbox_client.put_file(
                                    "{path}".format(path=full_remote_path),
                                    file_obj=file_h,
                                    overwrite=overwrite_mode
                                )
                        else:
                            uploader = dropbox_client.get_chunked_uploader(file_h, self.size)
                            while uploader.offset < self.size:
                                upload = uploader.upload_chunked()
                            uploader.finish(
                                "{path}".format(path=full_remote_path),
                                overwrite=overwrite_mode
                            )
                self.uploaded = True

            except Exception as e:
                logging.error("Could not back up NFile '{local_path}' to '{remote_path}'".format(local_path=path, remote_path=full_remote_path))
                logging.error("{}".format(e))
                logging.error("Skipping NFile {local_path}".format(local_path=path))
        else:
            logging.debug("NFile `{}` already fully uploaded".format(self.generate_full_path(source_base)))

    def __repr__(self):
        return "<NFile (name={}, uploaded={}, parent_name={})>".format(self.name, self.uploaded, self.parent.name)


class NFolder(NFile):
    # Folder is a special type of file
    children = None

    # We use JSON not Pickle for Metadata in case our class definition changes
    METADATA_FILENAME = ".dropboxbackupmeta"
    LASTUPLOAD_FILENAME = ".dropboxbackuplastupload"

    def __init__(self, parent, name, stats):
        super(NFolder, self).__init__(parent, name, stats)
        self.children = []

    def encodable(self, max_recurse_depth=-1, only_uploaded=False):
        d = super(NFolder, self).encodable()
        if max_recurse_depth != 0:
            # max_recurse_depth of -1 gives us an infinite recurse depth
            d["children"] = [c.encodable(max_recurse_depth-1, only_uploaded) for c in self.children if c.uploaded or only_uploaded is not True]
        d["_type"] = "NFolder"
        return d

    def get_metadata_from_path(self, ff_path):
        stats = os.stat(ff_path)
        return {
            'uid': stats.st_uid,
            'gid': stats.st_gid,
            'mode': stats.st_mode,
            'mtime': stats.st_mtime,
            'ctime': stats.st_ctime,
            'size': stats.st_size
        }

    def walk_local_tree_r (self, source_base, max_recurse_depth=-1):
        logging.debug("Node.walk_local_tree_r: Recurse depth {}".format(max_recurse_depth))
        # Recursively construct a local node tree
        # We assume someone's already checked we're a directory
        path = self.generate_path()
        full_local_path = os.path.join(source_base, path)
        logging.debug("Walking NFolder '{local_path}'".format(local_path=full_local_path))

        if os.path.islink(full_local_path):
            self.symlink_target = os.readlink(full_local_path)
            logging.debug("NFolder is symlink to {}".format(self.symlink_target))
            

        if not self.symlink_target:
            # Don't want to follow symlinks
            for g in glob.iglob(os.path.join(full_local_path, "*")):
                name = os.path.basename(g)
                stats = self.get_metadata_from_path(g)

                if os.path.isdir(g):
                    new_folder = NFolder(self, name, stats)
                    if max_recurse_depth != 0:
                        new_folder.walk_local_tree_r(source_base, max_recurse_depth-1)
                    self.children.append(new_folder)
                else:
                    new_file = NFile(self, name, stats)
                    logging.debug("Located NFile `{}`".format(name))
                    if os.path.islink(g):
                        new_file.symlink_target = os.readlink(g)
                    self.children.append(new_file)

    def rewrite_index_without_assumption_tree_r(self, dropbox_client, target, target_base="/", rewrite_index=True, max_recurse_depth=-1):
        # Reconstruct a node tree and index without assuming that the provided metadata file exists
        # We recurse the file tree and look for:
        #   - Metadata files in subfolders, when we don't have a metadata file in our folder
        #   - Metadata in subfolders that aren't listed as having metadata
        #   - Files listed in the metadata that aren't located in our metadata
        # We don't try to compare the metadata to the file itself
        # so if you suspect a remote file does not match the metadata, your best bet is to delete it and rebuild the index

        # Determine if we or a child has a metadata file
        # We do this so we don't just end up constructing an empty tree of all the folders
        # unless at least one folder actually *does* have metadata

        logging.debug("Node.rewrite_index_without_assumption_tree_r: Recurse depth {}".format(max_recurse_depth))

        someone_has_meta = False

        path = self.generate_path()
        target_path = "/".join([target_base, path])
        full_remote_path = "/{target}/data{path}".format(target=target, path=target_path)

        logging.debug("Walking remote NFolder '{full_remote_path}'".format(full_remote_path=full_remote_path))


        # Let's start by checking if *we* have any metadata
        remote_metadata = {}
        try:
            metadata_h = StringIO.StringIO()
            with dropbox_client.get_file("{folder_path}/{metadata_filename}".format(folder_path=full_remote_path, metadata_filename=self.METADATA_FILENAME)) as f:
                metadata_h.write(f.read())
            metadata_h.seek(0)
            remote_metadata = json.load(metadata_h)
            metadata_h.close()
            someone_has_meta = True
        except dropbox.rest.ErrorResponse as e:
            # We don't care if it isn't found, that's exactly why we're doing this whole thing
            if not e.status==404:
                raise
        except Exception as e:
            logging.warning("Could not get remote metadata for '{full_remote_path}'".format(full_remote_path=full_remote_path))
            logging.warning(e)
            logging.warning(traceback.format_exc())


        # We assume someone's already checked we're a directory
        # Also, our own stats will be set by our parent (later)

        try:
            # Get the children
            metadata_children = {child["name"]: child for child in (remote_metadata["children"] if "children" in remote_metadata else [])}
            stat_template = {
                "uid":  None,
                "gid": None,
                "mode": None,
                "mtime": None,
                "ctime": None,
                "size": None
            }
            metadata = dropbox_client.metadata(full_remote_path)
            for child in metadata["contents"]:
                try:
                    # We already know the path, just get the name
                    # The decode can be necessary
                    # TODO: Support UTF-8
                    name = child["path"].split("/")[-1].decode('utf-8')             
                    # Okay, it's a symlink, remove .symlink from the name
                    name = name[:-8] if name.endswith(".symlink") else name
                    if child["is_dir"]:
                        # It's a folder!
                        stat_array = metadata_children[name]["stats"] if name in metadata_children else stat_template
                        new_folder = NFolder(self, name, stat_array)
                        new_folder.uploaded = True
                        grandchild_has_meta = False
                        if max_recurse_depth != 0:
                            grandchild_has_meta = new_folder.rewrite_index_without_assumption_tree_r(dropbox_client, target, target_base, rewrite_index, max_recurse_depth-1)
                        if max_recurse_depth == 0 or grandchild_has_meta:
                            # Someone below us was a legit backup, or we're assuming they do, so we need to include them!
                            # If we're wrong, this'll be picked up in the next backup anyway.
                            self.children.append(new_folder)
                            someone_has_meta = True
                    else:
                        # It's a file (probably)!
                        if name == self.METADATA_FILENAME:
                            # We have a metadata file. Don't include in the node construction though
                            pass
                        else:

                            if name in metadata_children:
                                # Oh good, we know what to do with it
                                child_meta = metadata_children[name]
                                child_node = None
                                if child_meta["_type"] == "NFolder":
                                    # This can happen if it's a symlink
                                    child_node = NFolder(self, name, child_meta["stats"])
                                else:
                                    child_node = NFile(self, name, child_meta["stats"])
                                child_node.uploaded = True
                                if "symlink_target" in child_meta:
                                    child_node.symlink_target = child_meta["symlink_target"]
                                self.children.append(child_node)
                            else:
                                # Oh dear, we don't know anything useful about this file. We have to skip it
                                pass
                except Exception as e:
                    logging.warning("Error verifying a child of '{full_remote_path}'".format(full_remote_path=full_remote_path))
                    logging.warning(e)

        except Exception as e:
            logging.warning("Error verifying '{full_remote_path}'".format(full_remote_path=full_remote_path))
            logging.warning(e)
            logging.warning(traceback.format_exc())

        # Righto! At this point, our node is the definitive index, we should be able to rewrite the index!
        if rewrite_index and someone_has_meta:
            logging.info("Rebuilt index for {}".format(full_remote_path))
            try:
                # Right now generate the final metadata structure for this folder
                metadata_h = StringIO.StringIO()
                json.dump(self.encodable(max_recurse_depth=1, only_uploaded=True), metadata_h)
                dropbox_client.put_file("{folder_path}/{metadata_filename}".format(folder_path=full_remote_path, metadata_filename=self.METADATA_FILENAME), file_obj=metadata_h, overwrite=True)
                metadata_h.close()
            except Exception as e:
                # Marking as false will stop us being added to index, because we're not valid at this point. 
                # A rebuild *might* help
                someone_has_meta = False

                logging.error("Could not rewrite index for NFolder '{remote_path}'".format(remote_path=full_remote_path))
                logging.error("{}".format(e))
                logging.error(traceback.format_exc())

        return someone_has_meta

    def walk_remote_tree_r(self, dropbox_client, target, target_base="/", max_recurse_depth=-1):
        logging.debug("Node.walk_remote_tree_r: Recurse depth {}".format(max_recurse_depth))

        # Recursively construct a remote node tree based on remote metadata
        if not self.symlink_target:
            path = self.generate_path()
            target_path = "/".join([target_base, path])
            full_remote_path = "/{target}/data{path}".format(target=target, path=target_path)

            logging.debug("Walking remote NFolder '{full_remote_path}'".format(full_remote_path=full_remote_path))

            # We assume someone's already checked we're a directory
            # Also, our own stats are set by our parent. If we don't manage to process,
            # we end up with no children basically.
            remote_metadata = {}
            try:
                metadata_h = StringIO.StringIO()
                with dropbox_client.get_file("{folder_path}/{metadata_filename}".format(folder_path=full_remote_path, metadata_filename=self.METADATA_FILENAME)) as f:
                    metadata_h.write(f.read())
                metadata_h.seek(0)
                remote_metadata = json.load(metadata_h)
                metadata_h.close()
            except Exception as e:
                logging.warning("Could not get remote metadata for '{full_remote_path}'".format(full_remote_path=full_remote_path))
                logging.warning(e)
                logging.warning(traceback.format_exc())

            if remote_metadata:
                for child in remote_metadata["children"]:
                    try:
                        child_node = None
                        if child["_type"] == "NFile":
                            child_node = NFile(self, child["name"], child["stats"])
                        elif child["_type"] == "NFolder":
                            child_node = NFolder(self, child["name"], child["stats"])
                            if max_recurse_depth != 0 and not "symlink_target" in child:
                                child_node.walk_remote_tree_r(dropbox_client, target, target_base, max_recurse_depth-1)
                        else:
                            raise UnknownNodeTypeException()

                        if child_node:
                            child_node.uploaded = True
                            if "symlink_target" in child:
                                child_node.symlink_target = child["symlink_target"]
                            self.children.append(child_node)

                    except Exception as e:
                        name = child["name"] if "name" in child else "Unknown"
                        logging.warning("Error walking child <{child}> in '{full_remote_path}'".format(child=name, full_remote_path=full_remote_path))
                        logging.warning(e)
                        logging.warning(traceback.format_exc())

    def restore(self, local_base, dropbox_client, source, source_base="/", overwrite_mode=True, max_recurse_depth=-1):
        path = self.generate_path()
        full_local_path = os.path.join(local_base, path)
        source_path = "/".join([source_base, path])
        full_remote_path = "/{source}/data{path}".format(source=source, path=source_path)

        try:
            if not os.path.exists(full_local_path) or overwrite_mode:
                if not self.symlink_target:
                    # Opposite of upload - restore us first *then* walk the children
                    try:
                        os.mkdir(full_local_path)
                    except OSError as e:
                        # Check if it's an already exists error
                        if e.args[0] != 17:
                            raise
                    
                    if self.mode:
                        os.chmod(full_local_path, self.mode)

                    if self.uid and self.gid:
                        os.chown(full_local_path, self.uid, self.gid)

                    if self.mtime:
                        # Set atime to mtime as well
                        os.utime(full_local_path, (self.mtime, self.mtime))
                    if max_recurse_depth != 0:
                        for c in self.children:
                            c.restore(local_base, dropbox_client, source, source_base, overwrite_mode, max_recurse_depth-1)
                else:
                    os.symlink(self.symlink_target, full_local_path)
                    # Symlink stats don't actually matter
            else:
                logging.info("Path {} already exists, not overwriting it or its children".format(full_local_path))
        except Exception as e:
            logging.error("Could not restore NFolder {remote_path}' to '{local_path}'".format(local_path=path, remote_path=full_remote_path))
            logging.error("{}".format(e))
            logging.error(traceback.format_exc())
            logging.error("Skipping NFolder {remote_path}".format(remote_path=full_remote_path))

    def upload(self, source_base, dropbox_client, target, target_base="/", overwrite_mode=True, max_recurse_depth=-1):
        path = self.generate_path()
        full_local_path = os.path.join(source_base, path)
        target_path = "/".join([target_base, path])
        full_remote_path = "/{target}/data{path}".format(target=target, path=target_path)

        try:
            logging.debug("NFolder `{}` upload status is: {}".format(self.generate_full_path(source_base), self.uploaded))

            # Let's start by confirming the remote file/folder
            logging.debug("Enumerating NFolder '{local_path}'".format(local_path=path))
            
            if self.symlink_target:
                full_remote_path = "{name}.symlink".format(name=full_remote_path)

            meta = {}
            try:
                meta = dropbox_client.metadata(full_remote_path)
            except (dropbox.rest.ErrorResponse) as e:
                if e.status != 404:
                    raise e

            if not self.symlink_target and "is_dir" in meta and meta["is_dir"] is not True:
                # We care if the remote path is a dir and we're a folder, unless we're a symlink
                logging.warning("File exists at remote location; attempting removal...")
                dropbox_client.file_delete(full_remote_path)

            # Right, now to actually upload
            if self.symlink_target:
                if not self.uploaded:
                    # Let's start by confirming the remote file/folder
                    logging.info("Generating NFolder Symlink at '{remote_path}'".format(local_path=path, remote_path=full_remote_path))

                    # If we're a symlink, upload as symlink
                    response = dropbox_client.put_file(
                                    "{path}".format(path=full_remote_path),
                                    file_obj=self.symlink_target,
                                    overwrite=overwrite_mode
                                )

                    self.uploaded = True

            else:
                if not self.uploaded:
                    # Let's start by confirming the remote file/folder
                    logging.info("Uploading NFolder '{local_path}' to '{remote_path}'".format(local_path=path, remote_path=full_remote_path))
                    try:
                        dropbox_client.file_create_folder("/{target}/data/{path}".format(target=target, path=self.generate_path()))
                    except dropbox.rest.ErrorResponse as e:
                        # Ignore already created folder
                        if e.status != 403:
                            raise
                    self.uploaded = True
                else:
                    logging.debug("{} Already uploaded".format(full_remote_path))

                if max_recurse_depth != 0:
                    for c in self.children:
                        # max_recurse_depth of -1 gives us an infinite recurse depth
                        c.upload(source_base, dropbox_client, target, target_base, overwrite_mode=overwrite_mode, max_recurse_depth=max_recurse_depth-1)

                # Right now generate the final metadata structure for this folder
                metadata_h = StringIO.StringIO()
                json.dump(self.encodable(max_recurse_depth=1, only_uploaded=True), metadata_h)
                dropbox_client.put_file("{folder_path}/{metadata_filename}".format(folder_path=full_remote_path, metadata_filename=self.METADATA_FILENAME), file_obj=metadata_h, overwrite=True)
                metadata_h.close()


        except Exception as e:
            logging.error("Could not back up NFolder '{local_path}' to '{remote_path}'".format(local_path=path, remote_path=full_remote_path))
            logging.error("{}".format(e))
            logging.error(traceback.format_exc())
            logging.error("Skipping NFolder {local_path}".format(local_path=path))

    def __repr__(self):
        return "<NFolder (name={}, uploaded={}, parent_name={}, len(children)={})>".format(self.name, self.uploaded, self.parent.name, len(self.children))


class NRootFolder(NFolder):
    def __init__(self):
        self.children = []
        self.name=""
        self.parent=None

    def __repr__(self):
        return "<NRootFolder (len(children)={})>".format(len(self.children))


def main():
    logging.basicConfig(level=logging.INFO)
    import pprint
    root = NRootFolder()
    root.walk_local_tree_r(".")
    pprint.pprint(root.encodable())

if __name__ == '__main__':
    main()