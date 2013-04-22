# -*- coding: utf-8 -*-
# copyright 2007-2011 Brecht Machiels
# copyright 2009-2010 Chris Roberts
# copyright 2009-2011 Scott McCreary
# copyright 2009 Alexander Deynichenko
# copyright 2009 HaikuBot (aka RISC)
# copyright 2010-2011 Jack Laxson (Jrabbit)
# copyright 2011 Ingo Weinhold
# copyright 2013 Oliver Tappe

# -- Modules ------------------------------------------------------------------

from HaikuPorter.GlobalConfig import (globalConfiguration, 
									  readGlobalConfiguration)
from HaikuPorter.Port import Port
from HaikuPorter.RecipeTypes import Status
from HaikuPorter.Utils import check_output, sysExit, warn

import glob
import os
import re
import shutil
from subprocess import check_call
import sys


# -----------------------------------------------------------------------------

# regex to split recipe filenames into port / version
regExp = {}
regExp['portname'] = '^(?P<name>[^-/=!<>]+)'
regExp['portversion'] = '(?P<version>[\w.~]+)'
regExp['portfullname'] = regExp['portname'] + '-' + regExp['portversion']
regExp['recipefilename'] = regExp['portfullname'] + '\.recipe$'


# -- path to haikuports-tree --------------------------------------------------

svnPath = 'http://ports.haiku-files.org/svn/haikuports/trunk'


# -- naturalCompare -----------------------------------------------------------

def naturalCompare(left, right): 
	"""performs a natural compare between the two given strings - returns:
		-1 if left is lower than right
		 1 if left is higher than right
		 0 if both are equal"""
	
	convert = lambda text: int(text) if text.isdigit() else text.lower()
	alphanum_key = lambda key: [ convert(c) for c in re.split('([0-9]+)', key) ] 
	return cmp(alphanum_key(left), alphanum_key(right))

# -- bareVersionCompare -------------------------------------------------------

def bareVersionCompare(left, right):
	"""Compares two given bare versions - returns:
		-1 if left is lower than right
		 1 if left is higher than right
		 0 if both versions are equal"""

	leftElements = left.split('.')
	rightElements = right.split('.')

	index = 0
	leftElementCount = len(leftElements)
	rightElementCount = len(rightElements)
	while True:
		if index + 1 > leftElementCount:
			if index + 1 > rightElementCount:
				return 0
			else:
				return -1
		elif index + 1 > rightElementCount:
			return 1
			
		result = naturalCompare(leftElements[index], rightElements[index])
		if result != 0:
			return result
		
		index += 1
		
# -- versionCompare -----------------------------------------------------------

def versionCompare(left, right):
	"""Compares two given versions that may include a pre-release - returns 
		-1 if left is lower than right
		 1 if left is higher than right
		 0 if both versions are equal"""

	leftElements = left.split('~', 1)
	rightElements = right.split('~', 1)

	result = bareVersionCompare(leftElements[0], rightElements[0])
	if result != 0:
		return result
	
	if len(leftElements) < 2:
		if len(rightElements) < 2:
			return 0
		else:
			return -1
	elif len(rightElements) < 2:
		return 1
	
	# compare pre-release strings
	return naturalCompare(leftElements[1], rightElements[1])


# -- Main Class ---------------------------------------------------------------

class Main:
	def __init__(self, options, args):
		self.options = options
		
		# read global settings
		readGlobalConfiguration()
		
		# set up the global variables we'll inherit to the shell
		self._initGlobalShellVariables()
	
		self.treePath = globalConfiguration['TREE_PATH'].rstrip('/')
		
		# create path where built packages will be collected
		self.packagesPath = self.treePath + '/packages'
		if not os.path.exists(self.packagesPath):
			os.mkdir(self.packagesPath)

		# the path where repository will be built
		self.repositoryPath = self.treePath + '/repository'

		# if requested, list all ports in the HaikuPorts tree
		if self.options.list:
			self._searchPorts(None)
			sys.exit()

		# if requested, search for a port
		if self.options.search:
			if not args:
				sysExit('You need to specify a search string.\n'
						"Invoke '" + sys.argv[0] + " -h' for usage "
						"information.")
			self._searchPorts(args[0])
			sys.exit()
		
		if self.options.location:
			if not args:
				sysExit('You need to specify a search string.\n'
						"Invoke '" + sys.argv[0] + " -h' for usage "
						"information.")
			# Provide the installed location of a port (for quick editing)
			print os.path.join(self.treePath, self.searchPorts(args[0]))
			sys.exit()

		# if requested, checkout or update ports tree
		if self.options.get:
			self._updatePortsTree()
			sys.exit()

		# if requested, print the location of the haikuports source tree
		if self.options.tree:
			print self.treePath
			sys.exit()

		# if requested, scan the ports tree for problems
		if self.options.lint:
			self._checkSourceTree()
			sys.exit()

		# if there is no argument given, exit
		if not args:
			sysExit('You need to specify a search string.\n'
					"Invoke '" + sys.argv[0] + " -h' for usage information.")
		else:
			port = args[0]

		# split the argument into a port name and a version
		name, version = self._splitPortSpecIntoNameAndVersion(port)

		# find the port in the HaikuPorts tree
		category = self._getCategory(name)
		if category == None:
			sysExit('Port ' + name + ' not found in tree.')

		baseDir = self.treePath + '/' + category + '/' + name

		# if the port version was not specified, list available versions
		if version == None:
			versions = []
			reRecipeFile = re.compile(regExp['recipefilename'])
			dirList = os.listdir(baseDir)
			for item in dirList:
				m = reRecipeFile.match(item)
				if m:
					versions.append([m.group('version'), item])
			if len(versions) > 0:
				print 'Following versions of %s are available:' % name
				for version in versions:
					print '  ' + version[0]
				sysExit('Please run haikuporter again, specifying a port '
						+ 'version')
			else:
				sysExit('No recipe files for %s found.' % name)
	
		# don't build or package when not patching
		if not self.options.patch:
			self.options.build = False
			self.options.package = False

		# update repository if it exists and isn't empty, populate it otherwise
		if (os.path.isdir(self.repositoryPath) 
			and os.listdir(self.repositoryPath)):
			self._updateRepository()
		else:
			self._populateRepository()
			
		# do whatever's needed to the main port
		allPorts = self._getAllPorts()
		portID = name + '-' + version
		if portID not in allPorts:
			sysExit(portID + ' not found in tree.')
		self._doMainPort(allPorts[portID])

	def _doMainPort(self, port):
		"""Build/Unpack/... the port requested on the cmdline"""
			
		# read data from the recipe file
		if not port.revision:
			port.parseRecipeFile(True)

		# show port description, if requested
		if self.options.about:
			port.printDescription()
			sys.exit()

		# warn when the port is not stable on this architecture
		status = port.getStatusOnCurrentArchitecture()
		if status != Status.STABLE:
			warn('This port is %s on this architecture.' % status)
			if not self.options.yes:
				answer = raw_input('Continue (y/n + enter)? ')
				if answer == '':
					sys.exit(1)
				if answer[0].lower() == 'y':
					print ' ok'
				else:
					sys.exit(1)

		if port.recipeKeys['MESSAGE']:
			print port.recipeKeys['MESSAGE']
			if not self.options.yes:
				answer = raw_input('Continue (y/n + enter)? ')
				if answer == '':
					sys.exit(1)
				if answer[0].lower() == 'y':
					print ' ok'
				else:
					sys.exit(1)

		if self.options.build:
			self._buildMainPort(port)

		# TODO: reactivate these!
		# if self.options.test:
		#	port.test()

	def _buildMainPort(self, port):
		"""Build the port given on cmdline"""

		packageInfo = self.repositoryPath + '/' + port.packageInfoName
		if not os.path.exists(packageInfo):
			port.writePackageInfosIntoRepository(self.repositoryPath)
		
		buildDependencies \
			= port.resolveBuildDependencies(self.repositoryPath,
											self.packagesPath)
		allPorts = self._getAllPorts()
		requiredPortsToBuild = []
		for dependency in buildDependencies:
			print dependency
			if dependency.startswith(self.repositoryPath):
				packageInfoFileName = os.path.basename(dependency)
				requiredPortID \
					= packageInfoFileName[:packageInfoFileName.rindex('.')]
				try:
					requiredPort = allPorts[requiredPortID]
					requiredPortsToBuild.append(requiredPort)
				except KeyError:
					sysExit('Inconsistency: ' + port.versionedName
							 + ' requires ' + requiredPortID 
							 + ' but that does not exist!')

		if requiredPortsToBuild:
			print 'The following required ports will be built first:'
			for requiredPort in requiredPortsToBuild:			
				print('\t' + requiredPort.category + '::' 
					  + requiredPort.versionedName)
			for requiredPort in requiredPortsToBuild:			
				self._buildPort(requiredPort, True)
				
		self._buildPort(port, False)

	def _buildPort(self, port, parseRecipe):
		"""Build a single port"""

		print '-' * 70
		print port.versionedName
		print '-' * 70
		
		# pass-on options to port
		port.forceOverride = self.options.force
		port.beQuiet = self.options.quiet
		port.avoidChroot = not self.options.chroot
		
		if parseRecipe:
			port.parseRecipeFile(True)

		# clean the work directory, if requested
		if self.options.clean:
			port.cleanWorkDirectory()

		port.downloadSource()
		port.checksumSource()
		port.unpackSource()
		if self.options.patch:
			port.patchSource()
			
		port.build(self.packagesPath, self.options.package)
	

	def _initGlobalShellVariables(self):
		# extract the package info from the system package
		output = check_output('package list /system/packages/haiku.hpkg'
			+ ' | grep -E "^[[:space:]]*[[:alpha:]]+:[[:space:]]+"', 
			shell=True)

		# get the haiku version
		match = re.search(r"provides:\s*haiku\s+=\s*(\S+)", output)
		if not match:
			sysExit('Failed to get Haiku version!')
		self.haikuVersion = match.group(1)

		# get the architecture
		match = re.search(r"architecture:\s*(\S+)", output)
		if not match:
			sysExit('Failed to get Haiku architecture!')
		self.architecture = match.group(1)

		self.shellVariables = {
			'haikuVersion': self.haikuVersion,
			'architecture': self.architecture,
			'jobs': str(self.options.jobs),
		}
		if self.options.quiet:
			self.shellVariables['quiet'] = '1'

	def _updatePortsTree(self):
		"""Get/Update the port tree via svn"""
		print 'Refreshing the port tree: %s' % self.treePath
		if os.path.exists(self.treePath + '/.svn'):
			check_call(['svn', 'update', self.treePath])
		else:
			check_call(['svn', 'checkout', svnPath, self.treePath])

	def _searchPorts(self, regExp):
		"""Search for a port in the HaikuPorts tree"""
		if regExp:
			reSearch = re.compile(regExp)
		os.chdir(self.treePath)
		dirList = os.listdir(self.treePath)
		for category in dirList:
			if os.path.isdir(category) and category[0] != '.':
				subdirList = os.listdir(category)
				# remove items starting with '.'
				subdirList.sort()
				for portName in subdirList:
					if (portName[0][0] != '.' 
						and (not regExp or reSearch.search(portName))):
						print category + '/' + portName

	def _splitPortSpecIntoNameAndVersion(self, portSpec):
		reWithVersion = re.compile(regExp['portfullname'])
		reWithoutVersion = re.compile(regExp['portname'] + '$')
		if reWithVersion.match(portSpec):  # with version
			m = reWithVersion.match(portSpec)
			return m.group('name'), m.group('version')
		elif reWithoutVersion.match(portSpec):
			m = reWithoutVersion.match(portSpec)
			return m.group('name'), None
		else:
			# invalid argument
			sysExit('Invalid port name ' + portSpec)

	def _getCategory(self, portName):
		"""Find location of the specified port in the HaikuPorts tree"""
		hierarchy = []
		os.chdir(self.treePath)
		dirList = os.listdir(self.treePath)
		for item in dirList:
			if os.path.isdir(item) and item[0] != '.' and '-' in item:
				subdirList = os.listdir(item)
				# remove items starting with '.'
				subdirList.sort()
				while subdirList[0][0] == '.':
					del subdirList[0]

				# locate port
				try:
					if subdirList.index(portName) >= 0:
						# port was found in the category specified by 'item'
						return item
				except ValueError:
					pass
				hierarchy.append([item, subdirList])
		return None

	def _checkSourceTree(self):
		print 'Checking HaikuPorts tree at: ' + self.treePath

		allPorts = self._getAllPorts()
		for portName in sorted(self._portVersionsByName.keys(), key=str.lower):
			for version in self._portVersionsByName[portName]:
				portID = portName + '-' + version
				port = allPorts[portID]
				print '%s   [%s]' % (portID, port.category)
				try:
					port.validateRecipeFile(True)
				except SystemExit as e:
					print e.code

	def _populateRepository(self):
		"""Remove and refill the repository with all PackageInfo-files from
		   parseable recipes"""

		if os.path.exists(self.repositoryPath):
			shutil.rmtree(self.repositoryPath)
		newRepositoryPath = self.repositoryPath + '.new'
		if os.path.exists(newRepositoryPath):
			shutil.rmtree(newRepositoryPath)
		os.mkdir(newRepositoryPath)
		print 'Populating repository ...'

		allPorts = self._getAllPorts()
		for portName in sorted(self._portVersionsByName.keys(), key=str.lower):
			for version in reversed(self._portVersionsByName[portName]):
				portID = portName + '-' + version
				port = allPorts[portID]
				try:
					sys.stdout.write(' ' * 60)
					sys.stdout.write('\r\t%s' % port.versionedName)
					sys.stdout.flush()
					port.parseRecipeFile(False)
					status = port.getStatusOnCurrentArchitecture()
					if status == Status.STABLE:
						if (port.checkFlag('build') 
							and not self.options.preserveFlags):
							print '   [build-flag reset]'
							port.unsetFlag('build')
						else:
							print
						port.writePackageInfosIntoRepository(newRepositoryPath)
						break;
					else:
						print(' is skipped, as it is %s on this architecture'
							  % status)
				except SystemExit:
					sys.stdout.write('\r')
					pass
		os.rename(newRepositoryPath, self.repositoryPath)

	def _updateRepository(self):
		"""Update all PackageInfo-files in the repository as needed"""
		
		print 'Checking if any recipes have changed ...'
		allPorts = self._getAllPorts()
		packageInfos = glob.glob(self.repositoryPath + '/*.PackageInfo')
		for packageInfo in packageInfos:
			packageInfoFileName = os.path.basename(packageInfo)
			packageID = packageInfoFileName[:packageInfoFileName.rindex('.')]
			portID = packageID

			# what we have in portID may be a packageID instead, in which case
			# we need to find the corresponding portID.
			if portID not in allPorts:
				# cut out subparts from the package name until we find a port
				# with that name:
				(portName, version) = portID.rsplit('-', 1)
				(portName, unused1, unused2) = portName.rpartition('_')
				while portName:
					portID = portName + '-' + version
					if portID in allPorts:
						break
					(portName, unused1, unused2) = portName.rpartition('_')
			
			if portID in allPorts:
				port = allPorts[portID]
				if (not os.path.exists(packageInfo)
					or (os.path.getmtime(port.recipeFilePath) 
						> os.path.getmtime(packageInfo))):
					if (port.checkFlag('build') 
						and not self.options.preserveFlags):
						port.unsetFlag('build')
						print('\tupdating package infos of ' + packageID
							  + '   [build-flag has been reset]')
					else:
						print '\tupdating package infos of ' + packageID
					port.writePackageInfosIntoRepository(self.repositoryPath)
			else:
				print '\tremoving ' + packageInfoFileName
				os.remove(packageInfo)

	def _getAllPorts(self):
		if hasattr(self, '_allPorts'):
			return self._allPorts

		# For now, we collect all ports into a dictionary that can be keyed
		# by name + '-' + version. Additionally, we keep a sorted list of 
		# available versions for each port name.
		self._allPorts = {}
		self._portVersionsByName = {}
		for category in sorted(os.listdir(self.treePath)):
			categoryPath = self.treePath + '/' + category
			if (not os.path.isdir(categoryPath) or category[0] == '.'
				or '-' not in category):
				continue
			for port in sorted(os.listdir(categoryPath)):
				portPath = categoryPath + '/' + port
				if not os.path.isdir(portPath) or port[0] == '.':
					continue
				for recipe in os.listdir(portPath):
					recipePath = portPath + '/' + recipe
					if (not os.path.isfile(recipePath) 
						or not recipe.endswith('.recipe')):
						continue
					reWithVersion = re.compile(regExp['recipefilename'])
					m = reWithVersion.match(recipe)
					if (m and m.group('name') and m.group('version')):
						name = m.group('name')
						version = m.group('version')
						if name not in self._portVersionsByName:
							self._portVersionsByName[name] = [ version ]
						else:
							self._portVersionsByName[name].append(version)
						self._allPorts[name + '-' + version] \
							= Port(name, version, category, portPath, 
								   self.shellVariables)
					else:
						# invalid argument
						print("Warning: Couldn't parse port/version info: " 
							  + recipe)

		# Sort version list of each port
		for portName in self._portVersionsByName.keys():
			self._portVersionsByName[portName].sort(cmp=versionCompare)

		return self._allPorts
