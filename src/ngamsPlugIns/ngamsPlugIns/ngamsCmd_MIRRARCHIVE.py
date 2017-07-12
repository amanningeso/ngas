#
#    ICRAR - International Centre for Radio Astronomy Research
#    (c) UWA - The University of Western Australia, 2012
#    Copyright by UWA (in the framework of the ICRAR)
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
#******************************************************************************
#
# "@(#) $Id: ngamsCmd_MIRRARCHIVE.py,v 1.6 2010/06/17 14:23:45 awicenec Exp $"
#
# Who       When        What
# --------  ----------  -------------------------------------------------------
# jagonzal  2010/17/01  Created
#
"""
NGAS Command Plug-In, implementing an Archive Command specific for Mirroring

This works in a similar way as the 'standard' ARCHIVE Command, but has been
simplified in a few ways:

  - No replication to a Replication Volume is carried out.
  - Target disks are selected randomly, disregarding the Streams/Storage Set
    mappings in the configuration. This means that 'volume load balancing' is
    provided.
  - Archive Proxy Mode is not supported.
  - No probing for storage availability is supported.
  - In general, less SQL queries are performed and the algorithm is more
    light-weight.
  - crc is computed from the incoming stream
  - ngas_files data is 'cloned' from the source file
"""

import binascii
import logging
import os

from ngamsLib import ngamsDiskInfo, ngamsDbCore
from ngamsLib.ngamsCore import TRACE, genLog, NGAMS_ONLINE_STATE, \
    mvFile, getFileCreationTime, NGAMS_FILE_STATUS_OK, toiso8601
from . import ngamsFailedDownloadException
from . import ngamsDAPIMirroring
from . import ngamsCmd_RSYNCFETCH
from . import ngamsCmd_HTTPFETCH


logger = logging.getLogger(__name__)

def getTargetVolume(srvObj):
    """
    Get a random target volume with availability.

    srvObj:         Reference to NG/AMS server class object (ngamsServer).

    Returns:        Target volume object or None (ngamsDiskInfo | None).
    """

    sqlQuery = "SELECT %s FROM ngas_disks nd WHERE completed=0 AND " + \
               "host_id={0} order by available_mb desc" % ngamsDbCore.getNgasDisksCols()
    res = srvObj.getDb().query2(sqlQuery, args=(srvObj.getHostId(),))
    if not res:
        return None
    else:
        return ngamsDiskInfo.ngamsDiskInfo().unpackSqlResult(res[0])

def updateDiskInfo(srvObj,
                   resDapi,
                   availSpace):
    """
    Update the row for the volume hosting the new file.

    srvObj:     Reference to NG/AMS server class object (ngamsServer).

    resDapi:    Result returned from the DAPI (ngamsDapiStatus).

    availSpace: Remaining space in disk (in mb)

    Returns:   Void.
    """
    TRACE()

    sqlQuery = "UPDATE ngas_disks SET " +\
               "number_of_files=(number_of_files + 1), " +\
               "available_mb = {0}, " +\
               "bytes_stored = (bytes_stored + {1}) WHERE " +\
               "disk_id = {2}"
    srvObj.getDb().query2(sqlQuery, args=(availSpace, resDapi.getFileSize(), resDapi.getDiskId()))


def saveInStagingFile(srvObj,
                      ngamsCfgObj,
                      reqPropsObj,
                      stagingFilename,
                      startByte):
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
    TRACE()

    blockSize = ngamsCfgObj.getBlockSize()
    fetchMethod = 'HTTP'
    if ngamsCfgObj.getVal("Mirroring[1].fetch_method"):
        fetchMethod = ngamsCfgObj.getVal("Mirroring[1].fetch_method")
    if fetchMethod == 'RSYNC':
        info = ngamsCmd_RSYNCFETCH.saveToFile(srvObj, ngamsCfgObj, reqPropsObj, stagingFilename,
                                  blockSize, startByte)
    else:
        info = ngamsCmd_HTTPFETCH.saveToFile(srvObj, ngamsCfgObj, reqPropsObj, stagingFilename,
                                  blockSize, startByte)
    return info

def calculateCrc(filename, blockSize):
    crc = 0
    try:
        fdin = open(filename, "r")
        buff = "-"
        while len(buffer) > 0:
            buff = fdin.read(blockSize)
            crc = binascii.crc32(buff, crc)
    finally:
        fdin.close()
    return crc

def handleCmd(srvObj, reqPropsObj):
    if srvObj.getState() == NGAMS_ONLINE_STATE:
        __handleCmd(srvObj, reqPropsObj)
    else:
        raise ngamsFailedDownloadException.AbortedException("Server is OFFLINE")

def __handleCmd(srvObj, reqPropsObj):
    """
    Handle the Mirroring Archive (MIRRARCHIVE) Command.

    srvObj:         Reference to NG/AMS server class object (ngamsServer).setState


    reqPropsObj:    Request Property object to keep track of actions done
                    during the request handling (ngamsReqProps).

    Returns:        Void.
    """
    TRACE()

    # Is this NG/AMS permitted to handle Archive Requests?
    logger.debug("Checking if this NG/AMS permitted to handle Archive Requests?")
    if (not srvObj.getCfg().getAllowArchiveReq()):
        errMsg = genLog("NGAMS_ER_ILL_REQ", ["Archive"])
        raise Exception, errMsg

    # Generate staging filename.
    stgFilename = reqPropsObj.getStagingFilename()
    logger.info("staging filename is: %s", stgFilename)
    startByte = 0
    if (os.path.exists(stgFilename) == 0):
        logger.debug('this is a new staging file')
    else:
        startByte = os.path.getsize(stgFilename)
        logger.debug('staging file already exists, requesting resumption of download from byte %d', startByte)

    # Set reference in request handle object to the read socket.
    try:
        # Retrieve file_id and file_version from request proposal
        file_id = reqPropsObj.fileinfo['fileId']
        file_version = reqPropsObj.fileinfo['fileVersion']
        logger.debug("Got file_id=%s and file_version=%s", file_id, file_version)

        # Retrieve file contents (from URL, archive pull, or by storing the body
        # of the HTTP request, archive push).
        logger.info("Saving in staging file: %s", stgFilename)
        stagingInfo = saveInStagingFile(srvObj, srvObj.getCfg(), reqPropsObj,
                                        stgFilename, startByte)
        reqPropsObj.incIoTime(stagingInfo[0])
        checksumPlugIn = "ngamsGenCrc32"
        checksum = stagingInfo[1]
    except ngamsFailedDownloadException.FailedDownloadException, e:
        raise
    except ngamsFailedDownloadException.PostponeException, e:
        raise
    except Exception, e:
        if getattr(e, 'errno', 0) == 28:
            # we can't resume, otherwise the same host will be used next time
            logger.warning("ran out of disk space during the download to %s. Marking as FAILURE", stgFilename)
            # TBD automatically mark the volume as completed
            # TBD try something more sophisticated with the file - other volumes on the same host?
            #     ot at least ARCHIVE in another host avoiding the download of WAN again. This is
            # particularly important if we just downloaded most of a 300GB file
            raise ngamsFailedDownloadException.FailedDownloadException(e)
        elif reqPropsObj.getBytesReceived() >= 0:
            logger.warning("the fetch has already downloaded data. marking as TORESUME")
            raise ngamsFailedDownloadException.PostponeException(e)
        else:
            logger.warning(3, "no data has been downloaded yet. Marking as FAILURE")
            raise ngamsFailedDownloadException.FailedDownloadException(e)

    # Invoke DAPI
    logger.info("Invoking DAPI")
    resDapi = ngamsDAPIMirroring.ngamsGeneric(srvObj, reqPropsObj)

    # Move file to final destination.
    logger.info("Moving file to final destination: %s", resDapi.getCompleteFilename())
    ioTime = mvFile(reqPropsObj.getStagingFilename(), resDapi.getCompleteFilename(), True)
    reqPropsObj.incIoTime(ioTime)

    # Check/generate remaining file info + update in DB.
    logger.info("Creating db entry")
    ts = toiso8601()
    creDate = toiso8601(getFileCreationTime(resDapi.getCompleteFilename()))
    sqlUpdate = "update ngas_disks set available_mb = available_mb - :1 / (1024 * 1024), bytes_stored = bytes_stored + :2 " +\
                "where disk_id = :3"
    srvObj.getDb().query(sqlUpdate, maxRetries = 0, parameters = [resDapi.getFileSize(), resDapi.getFileSize(), resDapi.getDiskId()])
    sqlQuery = "INSERT INTO ngas_files " +\
               "(disk_id, file_name, file_id, file_version, " +\
               "format, file_size, " +\
               "uncompressed_file_size, compression, " +\
               "ingestion_date, ignore, checksum, " +\
               "checksum_plugin, file_status, creation_date) "+\
               "VALUES " +\
               "(:1, :2, :3, :4," +\
               " :5, :6," +\
               " :7, :8," +\
               " :9, :10, :11," +\
               " :12, :13, :14)"
    parameters = [
        str(resDapi.getDiskId()), str(resDapi.getRelFilename()) , file_id, file_version,
        str(resDapi.getFormat()), str(resDapi.getFileSize()),
        str(resDapi.getUncomprSize()), str(resDapi.getCompression()),
        str(ts), str(0), str(checksum),
        str(checksumPlugIn), NGAMS_FILE_STATUS_OK, str(creDate)
    ]
    srvObj.getDb().query(sqlQuery, maxRetries = 0, parameters = parameters)

    # Final log message
    logger.info("Successfully handled Archive Pull Request for data file with URI: %s",
            reqPropsObj.getSafeFileUri())

    return

# EOF