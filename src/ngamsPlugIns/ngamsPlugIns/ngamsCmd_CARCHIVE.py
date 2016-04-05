#
#    ALMA - Atacama Large Millimiter Array
#    (c) European Southern Observatory, 2002
#    Copyright by ESO (in the framework of the ALMA collaboration),
#    All rights reserved
#
#    This library is free software; you can redistribute it and/or
#    modify it under the terms of the GNU Lesser General Public
#    License as published by the Free Software Foundation; either
#    version 2.1 of the License, or (at your option) any later version.
#
#    This library is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#    Lesser General Public License for more details.
#
#    You should have received a copy of the GNU Lesser General Public
#    License along with this library; if not, write to the Free Software
#    Foundation, Inc., 59 Temple Place, Suite 330, Boston,
#    MA 02111-1307  USA
#
"""
NGAS Command Plug-In, implementing a Container Archive Command.

This works similarly as the QARCHIVE Command, but archiving more than
one file in one request, and also creating the necessary containers
in the NGAS database. Because of this reason this module reuses
methods defined in the ngamsCmd_QARCHIVE module
"""

import os
import time

import ngamsCmd_QARCHIVE
from pccUt import PccUtTime
from ngamsLib.ngamsCore import TRACE, genLog, error, checkCreatePath, info, \
    NGAMS_ONLINE_STATE, NGAMS_IDLE_SUBSTATE, NGAMS_BUSY_SUBSTATE, \
    NGAMS_STAGING_DIR, genUniqueId, mvFile, getFileCreationTime, \
    NGAMS_FILE_STATUS_OK, getDiskSpaceAvail, NGAMS_HTTP_SUCCESS, NGAMS_SUCCESS
from ngamsLib import ngamsMIMEMultipart, ngamsHighLevelLib, ngamsFileInfo, ngamsLib
from ngamsServer import ngamsCacheControlThread


def saveInStagingFile(ngamsCfgObj,
                      reqPropsObj,
                      stagingFilename,
                      diskInfoObj):
    """
    Save the data ready on the HTTP channel, into the given Staging
    Area file.

    ngamsCfgObj:     NG/AMS Configuration (ngamsConfig).

    reqPropsObj:     NG/AMS Request Properties object (ngamsReqProps).

    stagingFilename: Staging Area Filename as generated by
                     ngamsHighLevelLib.genStagingFilename() (string).

    diskInfoObj:     Disk info object. Only needed if mutual exclusion
                     is required for disk access (ngamsDiskInfo).

    Returns:         Void.
    """
    T = TRACE()

    try:
        blockSize = ngamsCfgObj.getBlockSize()
        return saveFromHttpToFile(ngamsCfgObj, reqPropsObj, stagingFilename,
                                  blockSize, 1, diskInfoObj)
    except Exception, e:
        errMsg = genLog("NGAMS_ER_PROB_STAGING_AREA", [stagingFilename,str(e)])
        error(errMsg)
        raise Exception, errMsg


def saveFromHttpToFile(ngamsCfgObj,
                       reqPropsObj,
                       trgFilename,
                       blockSize,
                       mutexDiskAccess = 1,
                       diskInfoObj = None):
    """
    Save the data available on an HTTP channel into the given file.

    ngamsCfgObj:     NG/AMS Configuration object (ngamsConfig).

    reqPropsObj:     NG/AMS Request Properties object (ngamsReqProps).

    trgFilename:     Target name for file where data will be
                     written (string).

    blockSize:       Block size (bytes) to apply when reading the data
                     from the HTTP channel (integer).

    mutexDiskAccess: Require mutual exclusion for disk access (integer).

    diskInfoObj:     Disk info object. Only needed if mutual exclusion
                     is required for disk access (ngamsDiskInfo).

    Returns:         Tuple. Element 0: Time in took to write
                     file (s) (tuple).
    """
    T = TRACE()

    checkCreatePath(os.path.dirname(trgFilename))

    timer = PccUtTime.Timer()
    try:
        # Make mutual exclusion on disk access (if requested).
        if (mutexDiskAccess):
            ngamsHighLevelLib.acquireDiskResource(ngamsCfgObj, diskInfoObj.getSlotId())

        # Distinguish between Archive Pull and Push Request. By Archive
        # Pull we may simply read the file descriptor until it returns "".
        if (ngamsLib.isArchivePull(reqPropsObj.getFileUri()) and
            not reqPropsObj.getFileUri().startswith('http://')):
            # (reqPropsObj.getSize() == -1)):
            # Just specify something huge.
            info(3,"It is an Archive Pull Request/data with unknown size")
            remSize = int(1e11)
        elif reqPropsObj.getFileUri().startswith('http://'):
            info(3,"It is an HTTP Archive Pull Request: trying to get Content-Length")
            httpInfo = reqPropsObj.getReadFd().info()
            headers = httpInfo.headers
            hdrsDict = ngamsLib.httpMsgObj2Dic(''.join(headers))
            if hdrsDict.has_key('content-length'):
                remSize = int(hdrsDict['content-length'])
            else:
                info(3,"No HTTP header parameter Content-Length!")
                info(3,"Header keys: %s" % hdrsDict.keys())
                remSize = int(1e11)
        else:
            remSize = reqPropsObj.getSize()
            info(3,"Archive Push/Pull Request - Data size: %d" % remSize)

        fd = reqPropsObj.getReadFd()
        handler = ngamsMIMEMultipart.FilesystemWriterHandler(blockSize, True, trgFilename)
        parser = ngamsMIMEMultipart.MIMEMultipartParser(handler, fd, remSize, blockSize)
        parser.parse()
        deltaTime = timer.stop()

        fileDataList  = handler.getFileDataList()
        crcTime       = handler.getCrcTime()
        writingTime   = handler.getWritingTime()
        rootContainer = handler.getRoot()
        readingTime   = parser.getReadingTime()
        bytesRead     = parser.getBytesRead()
        ingestRate    = (float(bytesRead) / deltaTime)
        reqPropsObj.setBytesReceived(bytesRead)

        info(4,"Transfer time: %.3f s; CRC time: %.3f s; write time %.3f s" % (readingTime, crcTime, writingTime))

        return [deltaTime, rootContainer, fileDataList,ingestRate]

    finally:
        # Release disk resouce.
        if (mutexDiskAccess):
            ngamsHighLevelLib.releaseDiskResource(ngamsCfgObj, diskInfoObj.getSlotId())


def createContainers(container, parentContainer, srvObj):
    """
    Recursively creates the necessary entries in the ngas_containers table
    to store the given hierarchy of Container objects
    """

    containerName = container.getContainerName()
    ingestionDate = time.time()
    parentContainerId = str(parentContainer.getContainerId()) if parentContainer else None
    containerId = srvObj.getDb().createContainer(containerName,
                                                 containerSize=0,
                                                 ingestionDate=ingestionDate,
                                                 parentContainerId=parentContainerId,
                                                 parentKnownToExist=True)
    container.setContainerId(containerId)
    container.setIngestionDate(ingestionDate)

    # Recurse on our children
    for childContainer in container.getContainers():
        createContainers(childContainer, container, srvObj)


def handleCmd(srvObj,
              reqPropsObj,
              httpRef):
    """
    Handle the Quick Archive (QARCHIVE) Command.

    srvObj:         Reference to NG/AMS server class object (ngamsServer).

    reqPropsObj:    Request Property object to keep track of actions done
                    during the request handling (ngamsReqProps).

    httpRef:        Reference to the HTTP request handler
                    object (ngamsHttpRequestHandler).

    Returns:        (fileId, filePath) tuple.
    """
    T = TRACE()

    # Is this NG/AMS permitted to handle Archive Requests?
    info(3, "Is this NG/AMS permitted to handle Archive Requests?")
    if (not srvObj.getCfg().getAllowArchiveReq()):
        errMsg = genLog("NGAMS_ER_ILL_REQ", ["Archive"])
        raise Exception, errMsg
    srvObj.checkSetState("Archive Request", [NGAMS_ONLINE_STATE],
                         [NGAMS_IDLE_SUBSTATE, NGAMS_BUSY_SUBSTATE],
                         NGAMS_ONLINE_STATE, NGAMS_BUSY_SUBSTATE,
                         updateDb=False)

    # Get mime-type (try to guess if not provided as an HTTP parameter).
    info(3, "Get mime-type (try to guess if not provided as an HTTP parameter).")
    if (reqPropsObj.getMimeType() == ""):
        mimeType = ngamsHighLevelLib.\
                   determineMimeType(srvObj.getCfg(), reqPropsObj.getFileUri())
        reqPropsObj.setMimeType(mimeType)
    else:
        mimeType = reqPropsObj.getMimeType()

    ## Set reference in request handle object to the read socket.
    info(3, "Set reference in request handle object to the read socket.")
    if reqPropsObj.getFileUri().startswith('http://'):
        fileUri = reqPropsObj.getFileUri()
        readFd = ngamsHighLevelLib.openCheckUri(fileUri)
        reqPropsObj.setReadFd(readFd)

    # Determine the target volume, ignoring the stream concept.
    info(3, "Determine the target volume, ignoring the stream concept.")
    targDiskInfo = ngamsCmd_QARCHIVE.getTargetVolume(srvObj)
    if (targDiskInfo == None):
        errMsg = "No disk volumes are available for ingesting any files."
        error(errMsg)
        raise Exception, errMsg
    reqPropsObj.setTargDiskInfo(targDiskInfo)

    # Generate staging filename.
    info(3, "Generate staging filename from URI: %s" % reqPropsObj.getFileUri())
    if (reqPropsObj.getFileUri().find("file_id=") >= 0):
        file_id = reqPropsObj.getFileUri().split("file_id=")[1]
        baseName = os.path.basename(file_id)
    else:
        baseName = os.path.basename(reqPropsObj.getFileUri())
    stgFilename = os.path.join("/", targDiskInfo.getMountPoint(),
                               NGAMS_STAGING_DIR,
                               genUniqueId() + "___" + baseName)
    info(3, "Staging filename is: %s" % stgFilename)
    reqPropsObj.setStagingFilename(stgFilename)

    # Retrieve file contents (from URL, archive pull, or by storing the body
    # of the HTTP request, archive push).
    stagingInfo = saveInStagingFile(srvObj.getCfg(), reqPropsObj,
                                    stgFilename, targDiskInfo)
    ioTime = stagingInfo[0]
    rootContainer = stagingInfo[1]
    fileDataList = stagingInfo[2]
    ingestRate = stagingInfo[3]
    reqPropsObj.incIoTime(ioTime)

    createContainers(rootContainer, None, srvObj)

    from ngamsLib import ngamsPlugInApi
    import ngamsGenDapi

    parDic = {}
    ngamsGenDapi.handlePars(reqPropsObj, parDic)
    diskInfo = reqPropsObj.getTargDiskInfo()
    # Generate file information.
    info(3,"Generate file information")
    dateDir = PccUtTime.TimeStamp().getTimeStamp().split("T")[0]
    resDapiList = []

    containerSizes = {}

    for item in fileDataList:
        container = item[0]
        filepath = item[1]
        crc = item[2]

        containerId = str(container.getContainerId())
        basename = os.path.basename(filepath)
        fileId = basename

        fileVersion, relPath, relFilename,\
                     complFilename, fileExists =\
                     ngamsPlugInApi.genFileInfo(srvObj.getDb(),
                                                srvObj.getCfg(),
                                                reqPropsObj, diskInfo,
                                                filepath,
                                                fileId,
                                                basename, [dateDir])
        complFilename, relFilename = ngamsGenDapi.checkForDblExt(complFilename,
                                                    relFilename)

        # Keep track of the total size of the container
        uncomprSize = ngamsPlugInApi.getFileSize(filepath)
        if not containerSizes.has_key(containerId):
            containerSizes[containerId] = 0
        containerSizes[containerId] += uncomprSize

        mimeType = reqPropsObj.getMimeType()
        compression = "NONE"
        archFileSize = ngamsPlugInApi.getFileSize(filepath)

        resDapi = ngamsPlugInApi.genDapiSuccessStat(diskInfo.getDiskId(),
                                                     relFilename,
                                                     fileId,
                                                     fileVersion, mimeType,
                                                     archFileSize, uncomprSize,
                                                     compression, relPath,
                                                     diskInfo.getSlotId(),
                                                     fileExists, complFilename)
        # Move file to final destination.
        info(3, "Moving file to final destination")
        ioTime = mvFile(filepath,
                        resDapi.getCompleteFilename())
        reqPropsObj.incIoTime(ioTime)

        # Get crc info
        checksumPlugIn = "StreamCrc32"
        checksum = str(crc)
        info(3, "Invoked Checksum Plug-In: " + checksumPlugIn +\
                " to handle file: " + resDapi.getCompleteFilename() +\
                ". Result: " + checksum)

        # Get source file version
        # e.g.: http://ngas03.hq.eso.org:7778/RETRIEVE?file_version=1&file_id=X90/X962a4/X1
        info(3, "Get file version")
        file_version = resDapi.getFileVersion()
        if reqPropsObj.getFileUri().count("file_version"):
            file_version = int((reqPropsObj.getFileUri().split("file_version=")[1]).split("&")[0])

        # Check/generate remaining file info + update in DB.
        info(3, "Creating db entry")
        ts = PccUtTime.TimeStamp().getTimeStamp()
        creDate = getFileCreationTime(resDapi.getCompleteFilename())
        fileInfo = ngamsFileInfo.ngamsFileInfo().\
                   setDiskId(resDapi.getDiskId()).\
                   setFilename(resDapi.getRelFilename()).\
                   setFileId(resDapi.getFileId()).\
                   setFileVersion(file_version).\
                   setFormat(resDapi.getFormat()).\
                   setFileSize(resDapi.getFileSize()).\
                   setUncompressedFileSize(resDapi.getUncomprSize()).\
                   setCompression(resDapi.getCompression()).\
                   setIngestionDate(ts).\
                   setChecksum(checksum).setChecksumPlugIn(checksumPlugIn).\
                   setFileStatus(NGAMS_FILE_STATUS_OK).\
                   setCreationDate(creDate).\
                   setIoTime(reqPropsObj.getIoTime())
        fileInfo.write(srvObj.getHostId(), srvObj.getDb())

        # Add the file to the container
        srvObj.getDb().addFileToContainer(containerId, resDapi.getFileId(), True)

        # Update the container sizes
        for contSizeInfo in containerSizes.iteritems():
            srvObj.getDb().setContainerSize(contSizeInfo[0], contSizeInfo[1])

        # Inform the caching service about the new file.
        info(3, "Inform the caching service about the new file.")
        if (srvObj.getCachingActive()):
            diskId      = resDapi.getDiskId()
            fileId      = resDapi.getFileId()
            fileVersion = file_version
            filename    = resDapi.getRelFilename()
            ngamsCacheControlThread.addEntryNewFilesDbm(srvObj, diskId, fileId,
                                                       fileVersion, filename)

        # Update disk info in NGAS Disks.
        info(3, "Update disk info in NGAS Disks.")
        srvObj.getDb().updateDiskInfo(resDapi.getFileSize(), resDapi.getDiskId())

        resDapiList.append(resDapi)

    # Check if the disk is completed.
    # We use an approximate extimate for the remaning disk space to avoid
    # to read the DB.
    info(3, "Check available space in disk")
    availSpace = getDiskSpaceAvail(targDiskInfo.getMountPoint(), smart=False)
    if (availSpace < srvObj.getCfg().getFreeSpaceDiskChangeMb()):
        complDate = PccUtTime.TimeStamp().getTimeStamp()
        targDiskInfo.setCompleted(1).setCompletionDate(complDate)
        targDiskInfo.write(srvObj.getDb())

    # Request after-math ...
    srvObj.setSubState(NGAMS_IDLE_SUBSTATE)
    msg = "Successfully handled Archive Pull Request for data file " +\
          "with URI: " + reqPropsObj.getSafeFileUri()
    info(1, msg)
    srvObj.ingestReply(reqPropsObj, httpRef, NGAMS_HTTP_SUCCESS,
                       NGAMS_SUCCESS, msg, targDiskInfo)


    for resDapi in resDapiList:
        # Trigger Subscription Thread. This is a special version for MWA, in which we simply swapped MIRRARCHIVE and QARCHIVE
        # chen.wu@icrar.org
        msg = "triggering SubscriptionThread for file %s" % resDapi.getFileId()
        info(3, msg)
        srvObj.addSubscriptionInfo([(resDapi.getFileId(),
                                     resDapi.getFileVersion())], [])
        srvObj.triggerSubscriptionThread()

# EOF
