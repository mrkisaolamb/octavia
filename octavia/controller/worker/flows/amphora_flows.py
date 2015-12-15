# Copyright 2015 Hewlett-Packard Development Company, L.P.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#

from oslo_config import cfg
from taskflow.patterns import graph_flow
from taskflow.patterns import linear_flow
from taskflow import retry

from octavia.common import constants
from octavia.controller.worker.tasks import amphora_driver_tasks
from octavia.controller.worker.tasks import cert_task
from octavia.controller.worker.tasks import compute_tasks
from octavia.controller.worker.tasks import database_tasks
from octavia.controller.worker.tasks import network_tasks

CONF = cfg.CONF
CONF.import_group('controller_worker', 'octavia.common.config')


class AmphoraFlows(object):
    def __init__(self):
        # for some reason only this has the values from the config file
        self.REST_AMPHORA_DRIVER = (CONF.controller_worker.amphora_driver ==
                                    'amphora_haproxy_rest_driver')

    def get_create_amphora_flow(self):
        """Creates a flow to create an amphora.

        Ideally that should be configurable in the
        config file - a db session needs to be placed
        into the flow

        :returns: The flow for creating the amphora
        """
        create_amphora_flow = linear_flow.Flow(constants.CREATE_AMPHORA_FLOW)
        create_amphora_flow.add(database_tasks.CreateAmphoraInDB(
                                provides=constants.AMPHORA_ID))
        if self.REST_AMPHORA_DRIVER:
            create_amphora_flow.add(cert_task.GenerateServerPEMTask(
                                    provides=constants.SERVER_PEM))

            create_amphora_flow.add(
                database_tasks.UpdateAmphoraDBCertExpiration(
                    requires=(constants.AMPHORA_ID, constants.SERVER_PEM)))

            create_amphora_flow.add(compute_tasks.CertComputeCreate(
                requires=(constants.AMPHORA_ID, constants.SERVER_PEM),
                provides=constants.COMPUTE_ID))
        else:
            create_amphora_flow.add(compute_tasks.ComputeCreate(
                requires=constants.AMPHORA_ID,
                provides=constants.COMPUTE_ID))
        create_amphora_flow.add(database_tasks.MarkAmphoraBootingInDB(
            requires=(constants.AMPHORA_ID, constants.COMPUTE_ID)))
        wait_flow = linear_flow.Flow(constants.WAIT_FOR_AMPHORA,
                                     retry=retry.Times(CONF.
                                                       controller_worker.
                                                       amp_active_retries))
        wait_flow.add(compute_tasks.ComputeWait(
            requires=constants.COMPUTE_ID,
            provides=constants.COMPUTE_OBJ))
        wait_flow.add(database_tasks.UpdateAmphoraInfo(
            requires=(constants.AMPHORA_ID, constants.COMPUTE_OBJ),
            provides=constants.AMPHORA))
        create_amphora_flow.add(wait_flow)
        create_amphora_flow.add(database_tasks.ReloadAmphora(
            requires=constants.AMPHORA_ID,
            provides=constants.AMPHORA))
        create_amphora_flow.add(amphora_driver_tasks.AmphoraFinalize(
            requires=constants.AMPHORA))
        create_amphora_flow.add(database_tasks.MarkAmphoraReadyInDB(
            requires=constants.AMPHORA))

        return create_amphora_flow

    def _get_post_map_lb_subflow(self, prefix, role):
        """Set amphora type after mapped to lb."""

        sf_name = prefix + '-' + constants.POST_MAP_AMP_TO_LB_SUBFLOW
        post_map_amp_to_lb = linear_flow.Flow(
            sf_name)

        post_map_amp_to_lb.add(database_tasks.ReloadAmphora(
            name=sf_name + '-' + constants.RELOAD_AMPHORA,
            requires=constants.AMPHORA_ID,
            provides=constants.AMPHORA))

        if role == constants.ROLE_MASTER:
            post_map_amp_to_lb.add(database_tasks.MarkAmphoraMasterInDB(
                name=sf_name + '-' + constants.MARK_AMP_MASTER_INDB,
                requires=constants.AMPHORA))
        elif role == constants.ROLE_BACKUP:
            post_map_amp_to_lb.add(database_tasks.MarkAmphoraBackupInDB(
                name=sf_name + '-' + constants.MARK_AMP_BACKUP_INDB,
                requires=constants.AMPHORA))
        elif role == constants.ROLE_STANDALONE:
            post_map_amp_to_lb.add(database_tasks.MarkAmphoraStandAloneInDB(
                name=sf_name + '-' + constants.MARK_AMP_STANDALONE_INDB,
                requires=constants.AMPHORA))

        return post_map_amp_to_lb

    def _get_create_amp_for_lb_subflow(self, prefix, role):
        """Create a new amphora for lb."""

        sf_name = prefix + '-' + constants.CREATE_AMP_FOR_LB_SUBFLOW
        create_amp_for_lb_subflow = linear_flow.Flow(sf_name)

        create_amp_for_lb_subflow.add(database_tasks.CreateAmphoraInDB(
            name=sf_name + '-' + constants.CREATE_AMPHORA_INDB,
            provides=constants.AMPHORA_ID))

        if self.REST_AMPHORA_DRIVER:
            create_amp_for_lb_subflow.add(cert_task.GenerateServerPEMTask(
                name=sf_name + '-' + constants.GENERATE_SERVER_PEM,
                provides=constants.SERVER_PEM))

            create_amp_for_lb_subflow.add(
                database_tasks.UpdateAmphoraDBCertExpiration(
                    name=sf_name + '-' + constants.UPDATE_CERT_EXPIRATION,
                    requires=(constants.AMPHORA_ID, constants.SERVER_PEM)))

            create_amp_for_lb_subflow.add(compute_tasks.CertComputeCreate(
                name=sf_name + '-' + constants.CERT_COMPUTE_CREATE,
                requires=(constants.AMPHORA_ID, constants.SERVER_PEM),
                provides=constants.COMPUTE_ID))
        else:
            create_amp_for_lb_subflow.add(compute_tasks.ComputeCreate(
                name=sf_name + '-' + constants.COMPUTE_CREATE,
                requires=constants.AMPHORA_ID,
                provides=constants.COMPUTE_ID))

        create_amp_for_lb_subflow.add(database_tasks.UpdateAmphoraComputeId(
            name=sf_name + '-' + constants.UPDATE_AMPHORA_COMPUTEID,
            requires=(constants.AMPHORA_ID, constants.COMPUTE_ID)))
        create_amp_for_lb_subflow.add(database_tasks.MarkAmphoraBootingInDB(
            name=sf_name + '-' + constants.MARK_AMPHORA_BOOTING_INDB,
            requires=(constants.AMPHORA_ID, constants.COMPUTE_ID)))
        wait_flow = linear_flow.Flow(sf_name + '-' +
                                     constants.WAIT_FOR_AMPHORA,
                                     retry=retry.Times(CONF.
                                                       controller_worker.
                                                       amp_active_retries))
        wait_flow.add(compute_tasks.ComputeWait(
            name=sf_name + '-' + constants.COMPUTE_WAIT,
            requires=constants.COMPUTE_ID,
            provides=constants.COMPUTE_OBJ))
        wait_flow.add(database_tasks.UpdateAmphoraInfo(
            name=sf_name + '-' + constants.UPDATE_AMPHORA_INFO,
            requires=(constants.AMPHORA_ID, constants.COMPUTE_OBJ),
            provides=constants.AMPHORA))
        create_amp_for_lb_subflow.add(wait_flow)
        create_amp_for_lb_subflow.add(amphora_driver_tasks.AmphoraFinalize(
            name=sf_name + '-' + constants.AMPHORA_FINALIZE,
            requires=constants.AMPHORA))
        create_amp_for_lb_subflow.add(
            database_tasks.MarkAmphoraAllocatedInDB(
                name=sf_name + '-' + constants.MARK_AMPHORA_ALLOCATED_INDB,
                requires=(constants.AMPHORA, constants.LOADBALANCER_ID)))
        create_amp_for_lb_subflow.add(database_tasks.ReloadAmphora(
            name=sf_name + '-' + constants.RELOAD_AMPHORA,
            requires=constants.AMPHORA_ID,
            provides=constants.AMPHORA))

        if role == constants.ROLE_MASTER:
            create_amp_for_lb_subflow.add(database_tasks.MarkAmphoraMasterInDB(
                name=sf_name + '-' + constants.MARK_AMP_MASTER_INDB,
                requires=constants.AMPHORA))
        elif role == constants.ROLE_BACKUP:
            create_amp_for_lb_subflow.add(database_tasks.MarkAmphoraBackupInDB(
                name=sf_name + '-' + constants.MARK_AMP_BACKUP_INDB,
                requires=constants.AMPHORA))
        elif role == constants.ROLE_STANDALONE:
            create_amp_for_lb_subflow.add(
                database_tasks.MarkAmphoraStandAloneInDB(
                    name=sf_name + '-' + constants.MARK_AMP_STANDALONE_INDB,
                    requires=constants.AMPHORA))

        return create_amp_for_lb_subflow

    def _allocate_amp_to_lb_decider(self, history):
        """decides if the lb shall be mapped to a spare amphora

        :return: True if a spare amphora exists in DB
        """

        return history.values()[0] is not None

    def _create_new_amp_for_lb_decider(self, history):
        """decides if a new amphora must be created for the lb

        :return: True
        """

        return history.values()[0] is None

    def get_amphora_for_lb_subflow(
            self, prefix, role=constants.ROLE_STANDALONE):
        """Tries to allocate a spare amphora to a loadbalancer if none

        exists, create a new amphora.
        """

        sf_name = prefix + '-' + constants.GET_AMPHORA_FOR_LB_SUBFLOW

        # We need a graph flow here for a conditional flow
        amp_for_lb_flow = graph_flow.Flow(sf_name)

        # Setup the task that maps an amphora to a load balancer
        allocate_and_associate_amp = database_tasks.MapLoadbalancerToAmphora(
            name=sf_name + '-' + constants.MAP_LOADBALANCER_TO_AMPHORA,
            requires=constants.LOADBALANCER_ID,
            provides=constants.AMPHORA_ID)

        # Define a subflow for if we successfuly map an amphora
        map_lb_to_amp = self._get_post_map_lb_subflow(prefix, role)
        # Define a subflow for if we can't map an amphora
        create_amp = self._get_create_amp_for_lb_subflow(prefix, role)

        # Add them to the graph flow
        amp_for_lb_flow.add(allocate_and_associate_amp,
                            map_lb_to_amp, create_amp)

        # Setup the decider for the path if we can map an amphora
        amp_for_lb_flow.link(allocate_and_associate_amp, map_lb_to_amp,
                             decider=self._allocate_amp_to_lb_decider)
        # Setup the decider for the path if we can't map an amphora
        amp_for_lb_flow.link(allocate_and_associate_amp, create_amp,
                             decider=self._create_new_amp_for_lb_decider)

        return amp_for_lb_flow

    def get_delete_amphora_flow(self):
        """Creates a flow to delete an amphora.

        This should be configurable in the config file
        :returns: The flow for deleting the amphora
        :raises AmphoraNotFound: The referenced Amphora was not found
        """

        delete_amphora_flow = linear_flow.Flow(constants.DELETE_AMPHORA_FLOW)
        delete_amphora_flow.add(database_tasks.
                                MarkAmphoraPendingDeleteInDB(
                                    requires=constants.AMPHORA))
        delete_amphora_flow.add(database_tasks.
                                MarkAmphoraHealthBusy(
                                    requires=constants.AMPHORA))
        delete_amphora_flow.add(compute_tasks.ComputeDelete(
            requires=constants.AMPHORA))
        delete_amphora_flow.add(database_tasks.
                                DisableAmphoraHealthMonitoring(
                                    requires=constants.AMPHORA))
        delete_amphora_flow.add(database_tasks.
                                MarkAmphoraDeletedInDB(
                                    requires=constants.AMPHORA))
        return delete_amphora_flow

    def get_failover_flow(self):
        """Creates a flow to failover a stale amphora

        :returns: The flow for amphora failover
        """

        failover_amphora_flow = linear_flow.Flow(
            constants.FAILOVER_AMPHORA_FLOW)
        failover_amphora_flow.add(
            network_tasks.RetrievePortIDsOnAmphoraExceptLBNetwork(
                requires=constants.AMPHORA, provides=constants.PORTS))
        failover_amphora_flow.add(network_tasks.FailoverPreparationForAmphora(
            requires=constants.AMPHORA))
        failover_amphora_flow.add(database_tasks.
                                  MarkAmphoraPendingDeleteInDB(
                                      requires=constants.AMPHORA))
        failover_amphora_flow.add(database_tasks.
                                  MarkAmphoraHealthBusy(
                                      requires=constants.AMPHORA))
        failover_amphora_flow.add(compute_tasks.ComputeDelete(
            requires=constants.AMPHORA))
        failover_amphora_flow.add(database_tasks.
                                  DisableAmphoraHealthMonitoring(
                                      requires=constants.AMPHORA))
        failover_amphora_flow.add(database_tasks.MarkAmphoraDeletedInDB(
            requires=constants.AMPHORA))
        failover_amphora_flow.add(database_tasks.CreateAmphoraInDB(
                                  provides=constants.AMPHORA_ID))
        failover_amphora_flow.add(
            database_tasks.GetUpdatedFailoverAmpNetworkDetailsAsList(
                requires=(constants.AMPHORA_ID, constants.AMPHORA),
                provides=constants.AMPS_DATA))
        if self.REST_AMPHORA_DRIVER:
            failover_amphora_flow.add(cert_task.GenerateServerPEMTask(
                                      provides=constants.SERVER_PEM))
            failover_amphora_flow.add(compute_tasks.CertComputeCreate(
                requires=(constants.AMPHORA_ID, constants.SERVER_PEM),
                provides=constants.COMPUTE_ID))
        else:
            failover_amphora_flow.add(compute_tasks.ComputeCreate(
                requires=constants.AMPHORA_ID, provides=constants.COMPUTE_ID))
        failover_amphora_flow.add(database_tasks.UpdateAmphoraComputeId(
            requires=(constants.AMPHORA_ID, constants.COMPUTE_ID)))
        failover_amphora_flow.add(
            database_tasks.AssociateFailoverAmphoraWithLBID(
                requires=(constants.AMPHORA_ID, constants.LOADBALANCER_ID)))
        failover_amphora_flow.add(database_tasks.MarkAmphoraBootingInDB(
            requires=(constants.AMPHORA_ID, constants.COMPUTE_ID)))
        wait_flow = linear_flow.Flow(constants.WAIT_FOR_AMPHORA,
                                     retry=retry.Times(CONF.
                                                       controller_worker.
                                                       amp_active_retries))
        wait_flow.add(compute_tasks.ComputeWait(
            requires=constants.COMPUTE_ID,
            provides=constants.COMPUTE_OBJ))
        wait_flow.add(database_tasks.UpdateAmphoraInfo(
            requires=(constants.AMPHORA_ID, constants.COMPUTE_OBJ),
            provides=constants.AMPHORA))
        failover_amphora_flow.add(wait_flow)
        failover_amphora_flow.add(database_tasks.ReloadAmphora(
            requires=constants.AMPHORA_ID,
            provides=constants.FAILOVER_AMPHORA))
        failover_amphora_flow.add(amphora_driver_tasks.AmphoraFinalize(
            rebind={constants.AMPHORA: constants.FAILOVER_AMPHORA},
            requires=constants.AMPHORA))
        failover_amphora_flow.add(database_tasks.UpdateAmphoraVIPData(
            requires=constants.AMPS_DATA))
        failover_amphora_flow.add(database_tasks.ReloadLoadBalancer(
            requires=constants.LOADBALANCER_ID,
            provides=constants.LOADBALANCER))
        failover_amphora_flow.add(network_tasks.GetAmphoraeNetworkConfigs(
            requires=constants.LOADBALANCER,
            provides=constants.AMPHORAE_NETWORK_CONFIG))
        failover_amphora_flow.add(database_tasks.GetListenersFromLoadbalancer(
            requires=constants.LOADBALANCER, provides=constants.LISTENERS))
        failover_amphora_flow.add(database_tasks.GetVipFromLoadbalancer(
            requires=constants.LOADBALANCER, provides=constants.VIP))
        failover_amphora_flow.add(amphora_driver_tasks.ListenersUpdate(
            requires=(constants.LISTENERS, constants.VIP)))
        failover_amphora_flow.add(amphora_driver_tasks.AmphoraPostVIPPlug(
            requires=(constants.LOADBALANCER,
                      constants.AMPHORAE_NETWORK_CONFIG)))
        failover_amphora_flow.add(
            network_tasks.GetMemberPorts(
                rebind={constants.AMPHORA: constants.FAILOVER_AMPHORA},
                requires=(constants.LOADBALANCER, constants.AMPHORA),
                provides=constants.MEMBER_PORTS
            ))
        failover_amphora_flow.add(amphora_driver_tasks.AmphoraPostNetworkPlug(
            rebind={constants.AMPHORA: constants.FAILOVER_AMPHORA,
                    constants.PORTS: constants.MEMBER_PORTS},
            requires=(constants.AMPHORA, constants.PORTS)))
        failover_amphora_flow.add(amphora_driver_tasks.ListenersStart(
            requires=(constants.LISTENERS, constants.VIP)))
        failover_amphora_flow.add(database_tasks.MarkAmphoraAllocatedInDB(
            rebind={constants.AMPHORA: constants.FAILOVER_AMPHORA},
            requires=(constants.AMPHORA, constants.LOADBALANCER_ID)))

        return failover_amphora_flow

    def cert_rotate_amphora_flow(self):
        """Implement rotation for amphora's cert.

         1. Create a new certificate
         2. Upload the cert to amphora
         3. update the newly created certificate info to amphora
         4. update the cert_busy flag to be false after rotation

        :returns: The flow for updating an amphora
        """
        rotated_amphora_flow = linear_flow.Flow(
            constants.CERT_ROTATE_AMPHORA_FLOW)

        # create a new certificate, the returned value is the newly created
        # certificate
        rotated_amphora_flow.add(cert_task.GenerateServerPEMTask(
            provides=constants.SERVER_PEM))

        # update it in amphora task
        rotated_amphora_flow.add(amphora_driver_tasks.AmphoraCertUpload(
            requires=(constants.AMPHORA, constants.SERVER_PEM)))

        # update the newly created certificate info to amphora
        rotated_amphora_flow.add(database_tasks.UpdateAmphoraDBCertExpiration(
            requires=(constants.AMPHORA_ID, constants.SERVER_PEM)))

        # update the cert_busy flag to be false after rotation
        rotated_amphora_flow.add(database_tasks.UpdateAmphoraCertBusyToFalse(
            requires=constants.AMPHORA))

        return rotated_amphora_flow
