# Dropback

## Overview
*Dropback* is an application designed to back up whole directories from your local computer/server into Dropbox, while retaining key metadata such as file permissions and ownership.

[![Code Climate](https://codeclimate.com/github/jondlove/dropback/badges/gpa.svg)](https://codeclimate.com/github/jondlove/dropback)[![Build Status](https://travis-ci.org/jondlove/dropback.svg?branch=master)](https://travis-ci.org/jondlove/dropback)[![Coverage Status](https://coveralls.io/repos/jondlove/dropback/badge.svg?branch=master&service=github)](https://coveralls.io/github/jondlove/dropback?branch=master)
## How To Use
### Getting Started
- Login to (Dropbox Developer Console)[https://www.dropbox.com/developers] and create a new app
	- It needs to use the Dropbox API
	- For safety, create it with 'App Folder' access
	- Give the App any name you like [this is what it will use in your Dropbox/Apps folder]
- Edit `backup.py` on your system and add the App Key and Secret in the appropriate variables near the top of the file

You can now connect to your Dropbox!

### Connect to Dropbox
To backup to or restore from Dropbox, connect by using `backup.py connect` and follow the instructions

Note that by default, *Dropback* will look for Dropbox credentials in the folder from which it is running. If you wish for the credentials to be available to the whole system, create the folder `/etc/dropbox_backups.d/` and move the credentials file there

### Backup to Dropbox
To backup to Dropbox, run `backup.py backup` and follow the instructions

### Restore from Dropbox
To backup to Dropbox, run `backup.py restore` and follow the instructions

## Key Caveats
- Dropbox is not designed for high volume/high speed backups and large files as you might get from backing up a whole server; *Dropback* is designed for selective backups
- *Dropback* is more for 'last resort' backups and should not be your primary backup method - key file metadata and information cannot be maintained like a real filesystem backup.
- *Dropback* will not delete files in the backup that have been removed locally, or remove files locally that have been deleted in Dropbox
- It is possible that the index for .dropboxbackupmeta will get out of sync if (e.g.) you move around files in Dropbox. You should run `backup.py rebuild` every few backups to reconstruct the index from scratch

## Contributing/Maintenance
While pull requests will likely be reviewed, *Dropback* should not be considered actively maintained and is not directly accepting contributions at this time.

## Support
No support is provided for *Dropback*.